pocsag-mrt
==========

POCSAG Multichannel Realtime Decoder

Copyright (c) 2012 iZsh - izsh at fail0verflow.com

License Information
=====================
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.

Notes
=========
The usage should be self-expanatory.

You can either read the samples from a file or from an GrOsmoSDR compatible
device (USRP, FunCube dongle, RTL-based devices etc.)

If you read the sample from a RTL-sdr device, make sure you offset your
center frequency from the channel frequencies you read (to prevent from
the "DC effect").

In the GUI:
- you can enter multiple channel frequencies to simultaneously decode them.
- to visualize a given channel using the QT sinks you just select it from
the list.
- you can remove a given channel by selecting it and pressing the backspace key
- you can adjust the center frequency at any time

When starting the software from the command line, you can specify many default
arguments. One of particular interest is the '-C/--channelsfile' option:
it enables you to pre-load at startup, one frequency per line (engineering
notation accepted), a list of channel frequencies to monitor.

GNURadio
==========
The application was tested with gnuradio 3.6.2 but a lower version might work.
It also (mainly) depends on:
- GrExtras: https://github.com/guruofquality/grextras/wiki
- GrOsmoSDR: http://sdr.osmocom.org/trac/wiki/GrOsmoSDR
- PyQt4
- Python 2.7 (gnuradio doesn't work with Python 3.x)

It tries to use as much as possible the "new" features (e.g. message passing
and gnuradio blocks are implemented entirely in Python) to ease the prototyping
and developement and to show that C++ is not always a mandatory evil with
gnuradio.

A special note for MacOS X users/developers:
to compile gnuradio without hitting the annoying python "mismatched version"
crash (when running gnuradio-company for instance), use the following command
line (assuming you are using macports for most dependencies):

	% mkdir build
	% cd build
	% cmake -DCMAKE_INSTALL_PREFIX:PATH=/opt/local -DPYTHON_INCLUDE_DIR=/opt/local/Library/Frameworks/Python.framework/Versions/2.7/Headers -DPYTHON_LIBRARY=/opt/local/Library/Frameworks/Python.framework/Versions/2.7/lib/libpython2.7.dylib ..

Usage
=========

	POCSAG Multichannel Realtime Decoder -- iZsh (izsh at fail0verflow.com)
	usage: pocsag-mrt.py [-h] [-i INPUT_FILE] [-l] [-o OUTPUT_FILE] [-c FREQCORR]
	                     [-f CENTERFREQ] [-r SAMPLERATE] [-s SYMRATE]
	                     [-C CHANNELS_FILE]
	
	optional arguments:
	  -h, --help            show this help message and exit
	  -i INPUT_FILE, --input INPUT_FILE
	                        read the samples from a file (default: None)
	  -l, --loop            when reading from a file, loop the samples (default:
	                        false)
	  -o OUTPUT_FILE, --output OUTPUT_FILE
	                        save the samples to a file (default: None)
	  -c FREQCORR, --freqcorr FREQCORR
	                        set the frequency correction (ppm) (default: 0.0)
	  -f CENTERFREQ, --freq CENTERFREQ
	                        set the center frequency (default: 0.0)
	  -r SAMPLERATE, --samplerate SAMPLERATE
	                        set the samplerate (default: 1000000.0)
	  -s SYMRATE, --symrate SYMRATE
	                        set the symbol rate (default: 1200)
	  -C CHANNELS_FILE, --channelsfile CHANNELS_FILE
	                        read an initial channels list from a file (default:
	                        None)

POCSAG
========
- http://en.wikipedia.org/wiki/POCSAG
- CCIR Recommendation R-584-1 / Rec. ITU-R M.584-2:
http://www.itu.int/dms_pubrec/itu-r/rec/m/R-REC-M.584-2-199711-I!!PDF-E.pdf
