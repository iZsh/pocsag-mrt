#!/usr/bin/env python

# POCSAG Multichannel Realtime Decoder
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
from gnuradio import extras
from gnuradio import eng_notation
from gnuradio.eng_option import eng_option
import sys
import time
import argparse
import osmosdr
import pocsag

try:
	from gnuradio import qtgui
	from PyQt4 import QtGui, QtCore, uic
	import sip
except ImportError:
	print "Error: Program requires PyQt4 and gr-qtgui."
	sys.exit(1)

BANNER = "POCSAG Multichannel Realtime Decoder -- iZsh (izsh at fail0verflow.com)"

INI_FREQ_CORR = 0.0
INI_FREQ= 0.0
INI_SAMPLERATE = 1e6
INI_SYMRATE = 1200
SPS = 8 # signal per symbol

FFTSIZE = 2048
XLATING_CUTOFF = 10e3

class pocsag_msgsink(gr.block, QtCore.QObject):

	pocsag_pagermsg = QtCore.pyqtSignal(dict)

	def __init__(self):
		gr.block.__init__(
			self,
			name = "POCSAG message sink",
			in_sig = None,
			out_sig = None,
			has_msg_input = True
		)
		QtCore.QObject.__init__(self)

	def work(self, input_items, output_items):
		try:
			msg = self.pop_msg_queue()
			key = pmt.pmt_symbol_to_string(msg.key)
			txt = pmt.to_python(msg.value)
			if key == pocsag.POCSAG_ID:
				self.pocsag_pagermsg.emit(txt)
				return 1
			return 0
		except:
			return -1

# We can't derive from extras.stream_selector... hence the ugly workaround
class stream_selector:
	def __init__(self, num_inputs, size_of_items):
		self.ss = extras.stream_selector(gr.io_signature(num_inputs, num_inputs, size_of_items),
			gr.io_signature(1, 1, size_of_items))
		self.num_inputs = num_inputs
	def set_output(self, n):
		p = [-2] * self.num_inputs
		p[n] = 0
		self.ss.set_paths(p)

class main_window(QtGui.QMainWindow):

	backspacepressed = QtCore.pyqtSignal()

	def __init__(self, topblock, args):
		QtGui.QMainWindow.__init__(self)

		uic.loadUi('pocsag-mrt_main.ui', self)
		self.srcsink = uic.loadUi('pocsag-mrt_srcsink.ui')
		self.demodsink = uic.loadUi('pocsag-mrt_demodsink.ui')

		self.topblock = topblock
		self.args = args
		self.push_text(BANNER, QtCore.Qt.magenta)

		self.init_sink()

		# Connect the signals/slots etc.
		self.freq_list.installEventFilter(self)
		self.srcsink.installEventFilter(self)
		self.demodsink.installEventFilter(self)
		self.installEventFilter(self)
		self.centerfreq_edit.returnPressed.connect(self.centerfreq_edittext)
		self.addfreq_edit.returnPressed.connect(self.addfreq_edittext)
		self.symrate_edit.returnPressed.connect(self.symrate_edittext)
		self.samplerate_edit.returnPressed.connect(self.samplerate_edittext)
		self.freqcorr_edit.returnPressed.connect(self.freqcorr_edittext)
		self.freq_list.itemSelectionChanged.connect(self.select_freq)
		self.backspacepressed.connect(self.remove_selected_freq)
		self.debug_check.stateChanged.connect(self.debug_state)
		self.srcsink_check.stateChanged.connect(self.srcsink_state)
		self.demodsink_check.stateChanged.connect(self.demodsink_state)
		# General inits
		self.selected_freq = None
		self.freqs = dict()
		self.set_freqcorr(self.topblock.source.get_freq_corr())
		self.set_samplerate(self.topblock.source.get_sample_rate())
		self.set_centerfreq(self.topblock.source.get_center_freq())
		self.symrate_edit.setText("%d" % self.args.symrate)
		self.symrate = float(self.args.symrate)
		self.read_channels(args.channels_file)

	def init_sink(self):
		self.topblock.stop()
		self.topblock.wait()
		self.enable_selector_buttons(False)
		#
		# Source/premodulation
		#
		# Create the selector and connect the source to it
		self.sel_c = stream_selector(3, gr.sizeof_gr_complex)
		self.topblock.connect(self.topblock.source, (self.sel_c.ss, 0))
		self.srcsink.source_source_radio.setEnabled(True)
		self.sel_c.set_output(0)
		# Add the sink
		self.srcsink.grsink = qtgui.sink_c(FFTSIZE, gr.firdes.WIN_BLACKMAN_hARRIS,
			self.topblock.source.get_center_freq(), self.topblock.source.get_sample_rate(),
			"Source Signal", True, True, True, False)
		self.srcsink.grsink.set_update_time(0.1)
		self.topblock.connect(self.sel_c.ss, self.srcsink.grsink)
		self.srcsink.sink = sip.wrapinstance(self.srcsink.grsink.pyqwidget(), QtGui.QWidget)
		self.srcsink.horizontalLayout.addWidget(self.srcsink.sink)
		# add a button group for the radio buttons
		self.waveselc = QtGui.QButtonGroup(self.srcsink.verticalLayout)
		self.waveselc.addButton(self.srcsink.source_source_radio, 0)
		self.waveselc.addButton(self.srcsink.source_xlating_radio, 1)
		self.waveselc.addButton(self.srcsink.source_interpolator_radio, 2)
		self.waveselc.buttonClicked[int].connect(self.waveselc_toggled)
		self.srcsink.source_source_radio.setChecked(True)
		#
		# Demodulation
		#
		# Add the sink
		self.sel_f = stream_selector(4, gr.sizeof_float)
		self.sel_f.set_output(0)
		self.demodsink.grsink = qtgui.sink_f(FFTSIZE, gr.firdes.WIN_BLACKMAN_hARRIS,
			0, self.args.symrate * SPS, "Demodulated Signal", True, True, True, False)
		self.demodsink.grsink.set_update_time(0.1)
		self.topblock.connect(self.sel_f.ss, self.demodsink.grsink)
		self.demodsink.sink = sip.wrapinstance(self.demodsink.grsink.pyqwidget(), QtGui.QWidget)
		self.demodsink.horizontalLayout.addWidget(self.demodsink.sink)
		# Add the button group
		self.waveself = QtGui.QButtonGroup(self.demodsink.verticalLayout)
		self.waveself.addButton(self.demodsink.demodulation_quaddemod_radio, 0)
		self.waveself.addButton(self.demodsink.demodulation_lowpass_radio, 1)
		self.waveself.addButton(self.demodsink.demodulation_clockrecovery_radio, 2)
		self.waveself.addButton(self.demodsink.demodulation_bits_radio, 3)
		self.waveself.buttonClicked[int].connect(self.waveself_toggled)
		self.demodsink.demodulation_quaddemod_radio.setChecked(True)
		#
		self.topblock.start()

	def read_channels(self, channels_file):
		if not channels_file: return
		for freq in channels_file.readlines():
			self.addfreq(eng_notation.str_to_num(freq.strip()))

	def eventFilter(self, watched, event):
		if event.type() == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Backspace:
			self.backspacepressed.emit()
			return True
		if event.type() == QtCore.QEvent.Close and watched == self.srcsink:
			self.srcsink_check.setCheckState(QtCore.Qt.Unchecked)
			return True
		if event.type() == QtCore.QEvent.Close and watched == self.demodsink:
			self.demodsink_check.setCheckState(QtCore.Qt.Unchecked)
			return True
		if event.type() == QtCore.QEvent.Close and watched == self:
			self.srcsink_check.setCheckState(QtCore.Qt.Unchecked)
			self.demodsink_check.setCheckState(QtCore.Qt.Unchecked)
			# Keep processing the event, we want to close the app
		return False

	def enable_selector_buttons(self, enabled = True):
		# We never disable the source button
		self.srcsink.source_xlating_radio.setEnabled(enabled)
		self.srcsink.source_interpolator_radio.setEnabled(enabled)
		self.demodsink.demodulation_quaddemod_radio.setEnabled(enabled)
		self.demodsink.demodulation_lowpass_radio.setEnabled(enabled)
		self.demodsink.demodulation_clockrecovery_radio.setEnabled(enabled)
		self.demodsink.demodulation_bits_radio.setEnabled(enabled)

	def waveselc_toggled(self, Id):
		self.sel_c.set_output(Id)
		self.set_uisink_frequency_range()

	def waveself_toggled(self, Id):
		self.sel_f.set_output(Id)
		self.set_uisink_frequency_range()

	def push_text(self, text, color = QtCore.Qt.black):
		self.console.setTextColor(color)
		self.console.append(text)

	def push_pagermsg(self, txt):
		ch = "N/A" if txt["channel"] == None else txt["channel"]
		pagertext = "Pager message -- Channel %s, From pager %d (%d), TXT: %s" % (ch, txt["addr"], txt["fun"], txt["text"])
		if txt["endofmsg"]:
			self.push_text(pagertext, QtCore.Qt.blue)
		else:
			self.push_text(pagertext, QtCore.Qt.red)

	def set_uisink_frequency_range(self):
		if not  hasattr(self, 'freq') or not hasattr(self, 'freqshift') or not  hasattr(self, 'samplerate'):
			return
		if self.waveselc.checkedId() == 0: # the source
			self.srcsink.grsink.set_frequency_range(self.centerfreq, self.samplerate)
		elif self.waveselc.checkedId() == 1: # the shifted frequency
			self.srcsink.grsink.set_frequency_range(self.centerfreq + self.freqshift, self.samplerate)
		elif self.waveselc.checkedId() == 2: # interpolated/decimated
			self.srcsink.grsink.set_frequency_range(self.centerfreq + self.freqshift, self.symrate * SPS)
		if self.waveself.checkedId() == 0: # quad demod
			self.demodsink.grsink.set_frequency_range(0, self.symrate * SPS)
		elif self.waveself.checkedId() == 1: # lowpass
			self.demodsink.grsink.set_frequency_range(0, self.symrate * SPS)
		elif self.waveself.checkedId() == 2: # clock recovery
			self.demodsink.grsink.set_frequency_range(0, self.symrate)
		elif self.waveself.checkedId() == 3: # bits
			self.demodsink.grsink.set_frequency_range(0, self.symrate)

	def set_centerfreq(self, freq):
		self.centerfreq = freq
		self.centerfreq_edit.setText("%.3fM" % (self.centerfreq / 1e6))
		self.push_text("Setting center frequency to %.3fMhz" % (self.centerfreq / 1e6))
		self.update_freqs()
		self.topblock.source.set_center_freq(self.centerfreq)
		self.set_uisink_frequency_range()

	def update_freqs(self):
		for freq_txt, values in self.freqs.items():
			if abs(self.centerfreq - values["freq"]) > self.samplerate / 2.0:
				self.push_text("%s is outside of the bandwidth reach!" % freq_txt)
				self.remove_freq(freq_txt)

	def addfreq(self, freq):
		self.addfreq_edit.clearFocus()
		self.addfreq_edit.clear()
		freq_txt = "%.6fMHz" % (freq / 1e6)
		if freq_txt in self.freqs:
			self.push_text("%s is already monitored!" % freq_txt)
			return
		if abs(self.centerfreq - freq) > self.samplerate / 2.0:
			self.push_text("%s is outside of the bandwidth reach!" % freq_txt)
			return
		self.push_text("Monitoring %s" % freq_txt)
		freqshift = self.centerfreq - freq
		# reconfigure the flowgraph
		# We use stop()/wait() because lock()/unlock() seems to freeze the app
		# Can't find the reason...
		self.topblock.stop()
		self.topblock.wait()
		# self.topblock.lock()
		freq_xlating_fir_filter = gr.freq_xlating_fir_filter_ccc(1, (gr.firdes.low_pass(1.0, self.samplerate, XLATING_CUTOFF, XLATING_CUTOFF / 2)), freqshift, self.samplerate)
		pocsag_decoder = pocsag.pocsag_decoder(self.samplerate, channel_str = freq_txt, symbolrate = self.symrate, debug = self.debug_check.isChecked())
		msgsink = pocsag_msgsink() # FIXME: Shouldn't we use only one general msgsink?
		self.topblock.connect(self.topblock.source, freq_xlating_fir_filter, pocsag_decoder, msgsink)
		# Connect the QT signal from the msgsink to the UI
		msgsink.pocsag_pagermsg.connect(self.push_pagermsg)
		# self.topblock.unlock()
		self.topblock.start()
		# Save the blocks
		self.freqs[freq_txt] = {
			"freq": freq,
			"freq_xlating_fir_filter": freq_xlating_fir_filter,
			"pocsag_decoder": pocsag_decoder,
			"msgsink": msgsink,
			"uchar2float": gr.uchar_to_float() # we need a converter to connect it to the qtsink
		}
		self.freq_list.addItem(freq_txt)

	def disconnect_sink(self, freq):
		self.topblock.disconnect(self.freqs[freq]["freq_xlating_fir_filter"], (self.sel_c.ss, 1))
		self.topblock.disconnect(self.freqs[freq]["pocsag_decoder"].fractional_interpolator, (self.sel_c.ss, 2))
		self.topblock.disconnect(self.freqs[freq]["pocsag_decoder"].quadrature_demod, (self.sel_f.ss, 0))
		self.topblock.disconnect(self.freqs[freq]["pocsag_decoder"].low_pass_filter, (self.sel_f.ss, 1))
		self.topblock.disconnect(self.freqs[freq]["pocsag_decoder"].digital_clock_recovery_mm, (self.sel_f.ss, 2))
		self.topblock.disconnect(self.freqs[freq]["pocsag_decoder"].digital_binary_slicer_fb, self.freqs[freq]["uchar2float"], (self.sel_f.ss, 3))

	def connect_sink(self, freq):
		self.topblock.connect(self.freqs[freq]["freq_xlating_fir_filter"], (self.sel_c.ss, 1))
		self.topblock.connect(self.freqs[freq]["pocsag_decoder"].fractional_interpolator, (self.sel_c.ss, 2))
		self.topblock.connect(self.freqs[freq]["pocsag_decoder"].quadrature_demod, (self.sel_f.ss, 0))
		self.topblock.connect(self.freqs[freq]["pocsag_decoder"].low_pass_filter, (self.sel_f.ss, 1))
		self.topblock.connect(self.freqs[freq]["pocsag_decoder"].digital_clock_recovery_mm, (self.sel_f.ss, 2))
		self.topblock.connect(self.freqs[freq]["pocsag_decoder"].digital_binary_slicer_fb, self.freqs[freq]["uchar2float"], (self.sel_f.ss, 3))

	def select_freq(self):
		if len(self.freq_list.selectedItems()) == 0:
			return
		freq = str(self.freq_list.selectedItems()[0].text())
		# Stop the flowchart
		self.topblock.stop()
		self.topblock.wait()
		# self.topblock.lock()
		# Disconnect the old selection
		if self.selected_freq:
			self.disconnect_sink(self.selected_freq)
		# Connect the new selection
		self.connect_sink(freq)
		# Restart the flowgraph
		self.topblock.start()
		# self.topblock.unlock()
		# Adjust the UI info
		self.set_uisink_frequency_range()
		self.enable_selector_buttons(True)
		self.selected_freq = freq

	def remove_selected_freq(self):
		if self.selected_freq == None: return
		self.remove_freq(self.selected_freq)

	def remove_freq(self, freq):
		if freq == None or freq not in self.freqs: return
		self.push_text("Removing %s" % freq)
		self.topblock.stop()
		self.topblock.wait()
		if self.selected_freq == freq: self.disconnect_sink(freq)
		self.topblock.disconnect(self.topblock.source,
			self.freqs[freq]["freq_xlating_fir_filter"],
			self.freqs[freq]["pocsag_decoder"],
			self.freqs[freq]["msgsink"])
		self.topblock.start()
		self.enable_selector_buttons(False)
		del self.freqs[freq]
		self.set_uisink_frequency_range()
		if self.selected_freq == freq: self.selected_freq = None
		self.freq_list.takeItem(self.freq_list.row(self.freq_list.findItems(freq, QtCore.Qt.MatchExactly)[0]))

	def set_freqcorr(self, freqcorr):
		self.freqcorr = freqcorr
		self.topblock.source.set_freq_corr(self.freqcorr, 0)
		self.freqcorr_edit.setText("%.3f" % self.freqcorr)
		self.push_text("Setting freq. correction to %.3f ppm" % self.freqcorr)

	def set_samplerate(self, samplerate):
		self.samplerate = samplerate
		self.samplerate_edit.setText("%.3fM" % (self.samplerate / 1e6))
		self.push_text("Setting sample rate to %.3fMhz" % (self.samplerate / 1e6))
		self.update_freqs()
		self.set_uisink_frequency_range()	

	def centerfreq_edittext(self):
		# try:
			self.set_centerfreq(eng_notation.str_to_num(str(self.centerfreq_edit.text())))
		# except ValueError:
		# 	self.push_text("Bad center frequency value entered")

	def addfreq_edittext(self):
		try:
			self.addfreq(eng_notation.str_to_num(str(self.addfreq_edit.text())))
		except ValueError:
			self.push_text("Bad frequency value entered")

	def symrate_edittext(self):
		try:
			self.symrate = eng_notation.str_to_num(str(self.symrate_edit.text()))
			self.push_text("Setting symbol rate to %.3fbaud\n" % self.symrate)
		except ValueError:
			self.push_text("Bad symbol rate value entered\n")

	def samplerate_edittext(self):
		try:
			self.set_samplerate(eng_notation.str_to_num(str(self.samplerate_edit.text())))
		except ValueError:
			self.push_text("Bad sample rate value entered")

	def freqcorr_edittext(self):
		try:
			self.set_freqcorr(eng_notation.str_to_num(str(self.freqcorr_edit.text())))
		except ValueError:
			self.push_text("Bad Freq. correction value entered")

	def debug_state(self, state):
		for value in self.freqs.values():
			value["pocsag_decoder"].set_debug(state == QtCore.Qt.Checked)

	def srcsink_state(self, state):
		if state == QtCore.Qt.Checked:
			self.srcsink.show()
		else:
			self.srcsink.hide()

	def demodsink_state(self, state):
		if state == QtCore.Qt.Checked:
			self.demodsink.show()
		else:
			self.demodsink.hide()

class my_top_block(gr.top_block):
	def __init__(self, args):
		gr.top_block.__init__(self)
		self.source = osmosdr.source_c() if not args.input_file else osmosdr.source_c("file=%s,rate=%f,repeat=%s,freq=%f" % (args.input_file, args.samplerate, args.loop, args.centerfreq))
		self.source.set_freq_corr(args.freqcorr, 0)
		self.source.set_sample_rate(args.samplerate)
		self.source.set_center_freq(args.centerfreq, 0)
		self.source.set_gain_mode(0, 0)
		self.source.set_gain(10, 0)
		self.source.set_if_gain(24, 0)
		if args.output_file:
			self.file_sink = gr.file_sink(gr.sizeof_gr_complex, args.output_file)
			self.connect(self.source, self.file_sink)

if __name__ == "__main__":
	print BANNER
	parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
	parser.add_argument('-i', '--input', dest='input_file', action='store',
		help='read the samples from a file')
	parser.add_argument('-l', '--loop', dest='loop', action='store_const',
		const='true', default='false', help='when reading from a file, loop the samples')
	parser.add_argument('-o', '--output', dest='output_file', action='store',
		help='save the samples to a file')
	parser.add_argument('-c', '--freqcorr', dest='freqcorr', action='store',
		type=eng_notation.str_to_num, default=INI_FREQ_CORR, help='set the frequency correction (ppm)')
	parser.add_argument('-f', '--freq', dest='centerfreq', action='store',
		type=eng_notation.str_to_num, default=INI_FREQ, help='set the center frequency')
	parser.add_argument('-r', '--samplerate', dest='samplerate', action='store',
		type=eng_notation.str_to_num, default=INI_SAMPLERATE, help='set the samplerate')
	parser.add_argument('-s', '--symrate', dest='symrate', action='store',
		type=int, default=INI_SYMRATE, help='set the symbol rate')
	parser.add_argument('-C', '--channelsfile', dest='channels_file', type=file,
		help='read an initial channels list from a file')
	args = parser.parse_args()

	# init the flowgraph and run it
	tb = my_top_block(args)
	tb.start()
	# build and show the UI
	qapp = QtGui.QApplication(sys.argv)
	main_window = main_window(tb, args)
	main_window.show()
	# Run rabbit, run!
	qapp.exec_()
	tb.stop()

