# GNURadio block for POCSAG decoding
# Copyright (c) 2012 iZsh -- izsh at fail0verflow.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
from gruel import pmt
from gnuradio import gr
from gnuradio import digital
from gnuradio import extras
import numpy

POCSAG_ID = "POCSAG"
SYMRATE = 1200
FM_DEVIATION = 4500 # standard deviation is 4.5kHz
SPS = 8 # signal per symbol

# yeah I know, it's slow, and there are nice bit tricks to do this,
# but meh
def hamming_weight(val):
	return bin(val).count('1')

def is_evenparity(val):
	return True if hamming_weight(val) & 1 else False

# the code used by POCSAG is a (n=31,k=21) BCH Code with dmin=5,
# thus it could correct two bit errors in a 31-Bit codeword.
# It is a systematic code.
# The generator polynomial is: 
#   g(x) = x^10+x^9+x^8+x^6+x^5+x^3+1
# The parity check polynomial is: 
#   h(x) = x^21+x^20+x^18+x^16+x^14+x^13+x^12+x^11+x^8+x^5+x^3+1
#   g(x) * h(x) = x^n+1
# 
POCSAG_BCH_POLY = 0x769
POCSAG_BCH_N = 31
POCSAG_BCH_K = 21

def BCH_syndrome(data, BCH_POLY, BCH_N, BCH_K):
	mask = 1 << (BCH_N - 1)
	coeff = BCH_POLY << (BCH_K - 1)
	n = BCH_K

	s = data >> 1 # throw away parity bit
	while n > 0:
		if s & mask:
			s ^= coeff
		n -= 1
		mask >>= 1
		coeff >>= 1
	if is_evenparity(data):
		s |= 1 << (BCH_N - BCH_K)

	return s

def BCH_fix(data, BCH_POLY, BCH_N, BCH_K):
	for i in xrange(32):
		t = data ^ (1 << i)
		if BCH_syndrome(t, BCH_POLY, BCH_N, BCH_K) == 0:
			return t
	for i in xrange(32):
		for j in xrange(i + 1, 32):
			t = data ^ ((1 << i) | (1 << j))
			if BCH_syndrome(t, BCH_POLY, BCH_N, BCH_K) == 0:
				return t
	return data

# automata states
POCSAG_SEARCH_PREAMBLE_START = 0
POCSAG_SEARCH_PREAMBLE_END = 1
POCSAG_SYNC = 2
POCSAG_SEARCH_SYNC = 3
POCSAG_SYNCHED = 4
# Some constants
POCSAG_STD_SYNC = 0x7cd215d8
POCSAG_STD_IDLE = 0x7a89c197
POCSAG_WORDSIZE = 32
POCSAG_SOFTTHRESHOLD = 2
POCSAG_MAXWORD = 16

class pocsag_pktdecoder(gr.block):
	def __init__(self, channel_str = None, sendmsg = True, debug = False):
		gr.block.__init__(
				self,
				name = "pocsag",
				in_sig = [numpy.uint8],
				out_sig = None,
				num_msg_outputs = 1
		)
		self.channel_str = channel_str
		self.sendmsg = sendmsg
		self.debug = debug
		self.acc = 0
		self.bcnt = 0
		self.wcnt = -1
		self.reset_txtvars()
		# there's two ways/two automata:
		# - one search for the preamble and then for the SYNC word
		#   (this could be useful to extend to non-standard SYNC words)
		# - the other one directly looks for the SYNC word
		# To properly factorize the code, we just set the initial
		# automata state, and the code will take care of taking the
		# proper path
		self.init_state = POCSAG_SEARCH_SYNC
		self.state = self.init_state
		self.compute_syncmask(32)
		# self.compute_syncmask(576)

	def reset_txtvars(self):
		self.activetxt = False
		self.txt = ""
		self.num = ""
		self.txt_w = 0
		self.txt_bcnt = 0
		self.addr = 0
		self.fun = 0

	def compute_syncmask(self, length):
		self.preamble = 0
		assert(length % 2 == 0)
		for i in xrange(length / 2):
			self.preamble = (self.preamble << 2) | 0b10
		self.preamble_mask = 2 ** length - 1
		self.preamble_shifted = ((self.preamble << 1) | 1) & self.preamble_mask

	def send_txt(self, endofmsg = False):
		if self.sendmsg and (self.addr != 0 or len(self.txt) > 0):
			self.post_msg(0,
				pmt.pmt_string_to_symbol(POCSAG_ID),
				pmt.from_python(
					{
						"addr": self.addr,
						"fun": self.fun,
						"text": self.txt,
						"num": self.num,
						"endofmsg": endofmsg,
						"channel": self.channel_str
					})
				)

	def BCH_syndrome(self, data):
		return BCH_syndrome(data, POCSAG_BCH_POLY, POCSAG_BCH_N, POCSAG_BCH_K)

	def BCH_fix(self, data):
		return BCH_fix(data, POCSAG_BCH_POLY, POCSAG_BCH_N, POCSAG_BCH_K)

	def log(self, word, hammingw, status, txt):
		status_str = "OK" if status else "ERR"
		if self.debug:
			print "%2d %08x (%08x) %d %3s %s" % (self.wcnt, word, self.acc, hammingw, status_str, txt)

	def add_preamble_bit(self, b):
		self.add_bit(b, self.preamble_mask)

	def add_bit(self, b, mask = 0xFFFFFFFF):
		self.bcnt += 1
		self.acc = ((self.acc << 1) | int(~b & 1)) & mask

	def read_word(self, inp):
		assert(len(inp) >= POCSAG_WORDSIZE)
		self.acc = 0
		self.bcnt = 0
		for i in xrange(POCSAG_WORDSIZE):
			self.add_bit(inp[i])

	def push_text(self, data):
		for i in reversed(xrange(20)):
			b = (data >> i) & 1
			self.txt_w = (self.txt_w >> 1) | (b << 6)
			self.txt_bcnt += 1
			if self.txt_bcnt == 7:
				self.txt += chr(self.txt_w)
				self.txt_w = 0
				self.txt_bcnt = 0

	def push_num(self, data):
		num = ""
		for i in xrange(5):
			num += "0123456789*U -)("[(data >> (16 - 4 * i)) & 0xF]
		self.num += num
		return num

	def work(self, input_items, output_items):
		return {
			POCSAG_SEARCH_PREAMBLE_START: self.search_preamble_start,
			POCSAG_SEARCH_PREAMBLE_END: self.search_preamble_end,
			POCSAG_SYNC: self.sync,
			POCSAG_SEARCH_SYNC: self.search_sync,
			POCSAG_SYNCHED: self.synched
		}[self.state](input_items[0])

	# it is not really needed, we could directly look for the SYNC word
	# but let's keep this code, it could be useful in case something
	# is not using a standard SYNC word
	def search_preamble_start(self, inp):
		self.wcnt = -1
		self.reset_txtvars()
		for i in xrange(len(inp)):
			self.add_preamble_bit(inp[i])
			if self.acc == self.preamble or self.acc == self.preamble_shifted:
				print "Found preamble @ %d, with 0x%x" % (self.bcnt, self.acc)
				self.state = POCSAG_SEARCH_PREAMBLE_END
				return i + 1
		return len(inp)

	def search_preamble_end(self, inp):
		for i in xrange(len(inp)):
			self.add_preamble_bit(inp[i])
			if self.acc != self.preamble and self.acc != self.preamble_shifted:
				print "Found end of preamble @ %d, 0x%x " % (self.bcnt, self.acc)
				self.state = POCSAG_SYNC
				return i
		return len(inp)

	def search_sync(self, inp):
		self.wcnt = -1
		self.reset_txtvars()
		for i in xrange(len(inp)):
			self.add_bit(inp[i])
			hw = hamming_weight(self.acc ^ POCSAG_STD_SYNC)
			if hw <= POCSAG_SOFTTHRESHOLD:
				self.log(self.acc, hw, True, "=> SYNC")
				self.state = POCSAG_SYNCHED
				return i + 1
		return len(inp)

	def sync(self, inp):
		# wait till we have at least enough bit
		# this simplify the following code
		if len(inp) < POCSAG_WORDSIZE: 
			return 0
		self.wcnt = -1
		self.read_word(inp)
		hw = hamming_weight(self.acc ^ POCSAG_STD_SYNC)
		if hw <= POCSAG_SOFTTHRESHOLD:
			self.log(self.acc, hw, True, "=> SYNC")
			self.state = POCSAG_SYNCHED
		else:
			self.log(self.acc, hw, False, "=> lost sync!")
			self.send_txt(False)
			self.state = self.init_state
		return POCSAG_WORDSIZE

	def synched(self, inp):
		if len(inp) < POCSAG_WORDSIZE: 
			return 0
		self.wcnt += 1
		if self.wcnt >= POCSAG_MAXWORD:
			self.state = POCSAG_SYNC
			return 0
		self.read_word(inp)
		w = self.acc
		if self.BCH_syndrome(w) != 0:
			w = self.BCH_fix(self.acc)
		if self.BCH_syndrome(w):
			self.log(w, hamming_weight(self.acc ^ w), False, "=> lost sync!")
			self.send_txt(False)
			self.state = self.init_state
			return POCSAG_WORDSIZE
		assert(w != POCSAG_STD_SYNC)
		if w == POCSAG_STD_IDLE and self.activetxt:
			self.log(w, hamming_weight(self.acc ^ POCSAG_STD_IDLE), True, "=> IDLE (end of message)")
			self.send_txt(True)
			self.reset_txtvars()
		elif w == POCSAG_STD_IDLE:
			self.log(w, hamming_weight(self.acc ^ POCSAG_STD_IDLE), True, "=> IDLE")
		elif w & (1 << 31):
			self.decode_data(w)
		else:
			self.decode_addr(w)
		return POCSAG_WORDSIZE

	def decode_data(self, w):
		data = (w >> 11) & (2 ** 20 - 1)
		self.push_text(data)
		num = self.push_num(data)
		self.log(w, hamming_weight(self.acc ^ w), True, "=> NUM: |%s| - TXT: |%s|" % (num, self.txt))

	def decode_addr(self, w):
		self.reset_txtvars()
		self.activetxt = True
		self.addr = ((w >> 13) & (2 ** 18 - 1)) | (self.wcnt / 2)
		self.fun = (w >> 11) & 3
		self.log(w, hamming_weight(self.acc ^ w), True, "=> Pager %d (fun = %d)" % (self.addr, self.fun))

class pocsag_decoder(gr.hier_block2):
	def __init__(self, samplerate, symbolrate = SYMRATE, channel_str = None,
		sendmsg = True, debug = False,
		samplepersymbol = SPS, fmdeviation = FM_DEVIATION
		):

		gr.hier_block2.__init__(self, "pocsag",
			gr.io_signature(1, 1, gr.sizeof_gr_complex), gr.io_signature(1, 1, 1))

		self.samplerate = samplerate
		self.symbolrate = symbolrate
		self.sendmsg = sendmsg
		self.debug = debug
		self.samplepersymbol = samplepersymbol
		self.fmdeviation = fmdeviation

		self.fractional_interpolator = gr.fractional_interpolator_cc(0, 1.0 * samplerate / (symbolrate * samplepersymbol))
		self.quadrature_demod = gr.quadrature_demod_cf((symbolrate * samplepersymbol) / (fmdeviation * 4.0))
		self.low_pass_filter = gr.fir_filter_fff(1, gr.firdes.low_pass(1, symbolrate * samplepersymbol, symbolrate * 2, symbolrate / 2.0, gr.firdes.WIN_HAMMING, 6.76))
		self.digital_clock_recovery_mm = digital.clock_recovery_mm_ff(samplepersymbol, 0.03 * 0.03 * 0.3, 0.4, 0.03, 1e-4)
		self.digital_binary_slicer_fb = digital.binary_slicer_fb()
		self.pktdecoder = pocsag_pktdecoder(channel_str = channel_str, sendmsg = sendmsg, debug = debug)
		self.connect(self,
			self.fractional_interpolator,
			self.quadrature_demod,
			self.low_pass_filter,
			self.digital_clock_recovery_mm,
			self.digital_binary_slicer_fb,
			self.pktdecoder,
			self)

	def set_debug(self, debug = False):
		self.debug = debug
		self.pktdecoder.debug = debug
