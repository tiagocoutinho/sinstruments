# -*- coding: utf-8 -*-
#
# This file is part of the instrument simulator project
#
# Copyright (c) 2018 Tiago Coutinho
# Distributed under the MIT. See LICENSE for more info.

"""
Hamamatsu simulator helper classes

To create a Hamamatsu device use the following configuration as
a starting point:

.. code-block:: yaml

    name: streak_cam_sim
    devices:
        - class: RemoteEx
          module: hamamatsu
          data_port: 1002
          transports:
              - type: tcp
                url: :1001

To start the server you can do something like:

    $ python -m isim streak_cam_sim

A simple *nc* client can be used to connect to the instrument:

    $ nc 0 1001

"""

import re
import enum
import time
import inspect
import datetime
import collections

from isim.device import BaseDevice

import gevent
from gevent.server import StreamServer


cmd_re = re.compile('(?P<name>\w+)\((?P<args>.*)\)')


class Result(object):
    CODE = -1

    def __init__(self, *args):
        self.args = list(args)

    def __str__(self):
        return ','.join(map(str, [self.CODE] + self.args))


class Error(Result):
    pass


class InvalidSyntax(Error):
    CODE = 1

    def __init__(self, *args):
        args = list(args) + ['Invalid syntax']
        super(InvalidSyntax, self).__init__(*args)


class UnknownCommandOrParameters(Error):
    CODE = 2


class Ok(Result):
    CODE = 0


def command(f):
    name = f.__name__.replace('_', '').lower()
    f.command = { 'name': name }
    return f


class AcqMode(enum.Enum):
    Live = 0
    Acquire = 1
    AI = 2
    PC = 3


class MonitorType(enum.Enum):
    Off = 0
    Notify = 1
    NotifyTimeStamp = 2
    RingBuffer = 3
    Average = 4
    Minimum = 5
    Maximum = 6
    Profile = 7


class ParamType(enum.Enum):
    Boolean = (lambda x: x != '0', lambda x: '1' if x else '0')
    Numeric = (int, str)
    List = (lambda x: x.split(','), lambda x: ','.join(x))
    Text = (str, str)
    ExposureTime = (int, str)
    String = (str, str)


class ParamAccess(enum.Enum):
    Read = 0
    ReadWrite = 1


ParamInfo = collections.namedtuple('ParamInfo', ('label', 'type', 'default',
                                                 'access'))


def ParamInfoR(label, type, default=None):
    return ParamInfo(label, type, default, ParamAccess.Read)


def ParamInfoRW(label, type, default=None):
    return ParamInfo(label, type, default, ParamAccess.ReadWrite)


Param = collections.namedtuple('Param', ('value', 'info'))


AppParamInfo = {
    'type': ParamInfoR('App Type', ParamType.Text, 'HiPic'),
    'date': ParamInfoR('Date', ParamType.Text, str(datetime.date.today())),
    'version': ParamInfoR('Version', ParamType.Text, '8.3.0 pf8'),
    'directory': ParamInfoR('Directory', ParamType.Text,
                            'C:\\ProgramData\\Hamamatsu\\HiPic\\'),
    'title': ParamInfoR('Title', ParamType.Text, 'HiPic Emulator'),
    'titlelong': ParamInfoR('Long title', ParamType.Text, 'Bliss HiPic Emulator'),
    'appdatadir': ParamInfoR('App data directory', ParamType.Text,
                             'Windows 7, HiPic: C:\\ProgramData\\Hamamatsu\\HiPic\\')
}


MainParamInfo = {
    'imagesize': ParamInfoR('Image size', ParamType.Text, '1024x768'),
    'message': ParamInfoR('Message', ParamType.Text, ''),
    'temperature': ParamInfoR('Temperature', ParamType.Text, '22.5'),
    'gatemode': ParamInfoR('Gate mode', ParamType.Text, ''),
    'mcpgain': ParamInfoR('MCP gain', ParamType.Text, ''),
    'mode': ParamInfoR('Mode', ParamType.Text, ''),
    'plugin': ParamInfoR('Plugin', ParamType.Text, ''),
    'shutter': ParamInfoR('Shutter', ParamType.Text, ''),
    'streakcamera': ParamInfoR('Streak camera', ParamType.Text, ''),
    'timerange': ParamInfoR('Time range', ParamType.Text, '')
}


AcqParamInfo = {
    'displayinterval': ParamInfoRW('Display Interval [msec]', ParamType.Numeric, 100),
    '32bitinai': ParamInfoRW('32Bit in AI', ParamType.Boolean, 0),
    'writedpcfile': ParamInfoRW('Write DPC file', ParamType.Boolean, 0),
    'additionaltimeout': ParamInfoRW('Additional Timeout [sec]', ParamType.Numeric, 0),
    'deactivategrbnotituse': ParamInfoRW('Deactivate grabber not in use', ParamType.Boolean, 0),
    'ccdgainforpc': ParamInfoRW('CCD Gain for PC', ParamType.Numeric, 1),
    '32bitinpc': ParamInfoRW('32Bit in PC', ParamType.Boolean, 0),
    'moireereduction': ParamInfoRW('Moire reduction', ParamType.Boolean, 0),
}


CamParamInfo = {
    'setup': {
        'timingmode': ParamInfoRW('Timing mode (Internal / External)', ParamType.Text),
        'triggermode': ParamInfoRW('Trigger mode', ParamType.Text),
        'triggersource': ParamInfoRW('Trigger source', ParamType.Text),
        'triggerpolarity': ParamInfoRW('Trigger polarity', ParamType.Text),
        'scanmode': ParamInfoRW('Scan mode', ParamType.Text),
        'binning': ParamInfoRW('Binning factor', ParamType.Text),
        'ccdarea': ParamInfoRW('CCD area', ParamType.Text),
        'lightmode': ParamInfoRW('Light mode', ParamType.Text),
        'hoffs': ParamInfoRW('Horizontal Offset (Subarray)', ParamType.Text),
        'hwidth': ParamInfoRW('Horizontal Width (Subarray)', ParamType.Text),
        'voffs': ParamInfoRW('Vertical Offset (Subarray)', ParamType.Text),
        'vwidth': ParamInfoRW('Vertical Width (Subarray)', ParamType.Text),
        'showgainoffset': ParamInfoRW('Show Gain and Offset on acquisition dialog', ParamType.Text),
        'nolines': ParamInfoRW('Number of lines (TDI mode)', ParamType.Text),
        'linesperimage': ParamInfoRW('Number of lines (TDI mode)', ParamType.Text),
        'scrollinglivedisplay': ParamInfoRW('Scrolling or non scrolling live display', ParamType.Text),
        'frametrigger': ParamInfoRW('Frame trigger (TDI or X-ray line sensors)', ParamType.Text),
        'verticalbinning': ParamInfoRW('Vertical Binning (TDI mode)', ParamType.Text),
        'tapno': ParamInfoRW('Number of Taps (Multitap camera)', ParamType.Text),
        'shutteraction': ParamInfoRW('Shutter action', ParamType.Text),
        'cooler': ParamInfoRW('Cooler switch', ParamType.Text),
        'targettemperature': ParamInfoRW('Cooler target temperature', ParamType.Text),
        'contrastenhancement': ParamInfoRW('Contrast enhancement', ParamType.Text),
        'offset': ParamInfoRW('Analog Offset', ParamType.Text),
        'gain': ParamInfoRW('Analog Gain', ParamType.Text),
        'xdirection': ParamInfoRW('Pixel number in X direction', ParamType.Text),
        'offset': ParamInfoRW('Vertical Offset in Subarray mode', ParamType.Text),
        'width': ParamInfoRW('Vertical Width in Subarray mode', ParamType.Text),
        'scanspeed': ParamInfoRW('Scan speed', ParamType.Text),
        'mechanicalshutter': ParamInfoRW('Behavior of Mechanical Shutter', ParamType.Text),
        'subtype': ParamInfoRW('Subtype (X-Ray Flatpanel)', ParamType.Text),
        'autodetect': ParamInfoRW('Auto detect subtype', ParamType.Text),
        'wait2ndframe': ParamInfoRW('Wait for second frame in Acquire mode', ParamType.Text),
        'dx': ParamInfoRW('Image Width (Generic camera)', ParamType.Text),
        'dy': ParamInfoRW('Image height (Generic camera)', ParamType.Text),
        'xoffset': ParamInfoRW('X-Offset (Generic camera)', ParamType.Text),
        'yoffset': ParamInfoRW('Y-Offset (Generic camera)', ParamType.Text),
        'bpp': ParamInfoRW('Bits per Pixel(Generic camera)', ParamType.Text),
        'cameraname': ParamInfoRW('Camera name (Generic camera)', ParamType.Text),
        'exposuretime': ParamInfoRW('Exposure time (Generic camera)', ParamType.Text),
        'readouttime': ParamInfoRW('Readout time Generic camera)', ParamType.Text),
        'onchipamp': ParamInfoRW('On chip amplifier', ParamType.Text),
        'coolingfan': ParamInfoRW('Cooling fan', ParamType.Text),
        'cooler': ParamInfoRW('Coolier', ParamType.Text),
        'extoutputpolarity': ParamInfoRW('External output polarity', ParamType.Text),
        'extoutputdelay': ParamInfoRW('External output delay', ParamType.Text),
        'extoutputwidth': ParamInfoRW('External output width', ParamType.Text),
        'lowlightsensitivity': ParamInfoRW('Low light sensitivity', ParamType.Text),
        'tdimode': ParamInfoRW('TDI Mode', ParamType.Text),
        'binningx': ParamInfoRW('Binning X direction', ParamType.Text),
        'binningy': ParamInfoRW('Binning Y direction', ParamType.Text),
        'areaexposuretime': ParamInfoRW('Exposure time in area mode', ParamType.Text),
        'magnifying': ParamInfoRW('Use maginfying geometry', ParamType.Text),
        'objectdistance': ParamInfoRW('Object Distance', ParamType.Text),
        'sensordistance': ParamInfoRW('Sensor Distance', ParamType.Text),
        'conveyerspeed': ParamInfoRW('Conveyer speed', ParamType.Text),
        'linespeed': ParamInfoRW('Line speed', ParamType.Text),
        'linefrequency': ParamInfoRW('Line frequence', ParamType.Text),
        'exposuretime': ParamInfoRW('Exposure time in line scan mode', ParamType.Text),
        'displayduringmeasurement': ParamInfoRW('Display during measurement option', ParamType.Text),
        'gaintable': ParamInfoRW('Gain table', ParamType.Text),
        'nooftimestocheck': ParamInfoRW('Number of times to check', ParamType.Text),
        'maximumbackgroundlevel': ParamInfoRW('Maximum background level', ParamType.Text),
        'minimumsensitivitylevel': ParamInfoRW('Maximum sensitivity level', ParamType.Text),
        'fluctuation': ParamInfoRW('Fluctuation', ParamType.Text),
        'noofintegration': ParamInfoRW('Number of Integration', ParamType.Text),
        'dualenergycorrection': ParamInfoRW('Dual energy correction method', ParamType.Text),
        'lowenergyvalue': ParamInfoRW('Dual energy correction low energy value', ParamType.Text),
        'highenergyvalue': ParamInfoRW('Dual energy correction high energy value', ParamType.Text),
        'noofareaso': ParamInfoRW('Number of Ouput areas', ParamType.Text),
        'areastarto1': ParamInfoRW('Ouput area 1 start', ParamType.Text),
        'areastarto2': ParamInfoRW('Ouput area 2 start', ParamType.Text),
        'areastarto3': ParamInfoRW('Ouput area 3 start', ParamType.Text),
        'areastarto4': ParamInfoRW('Ouput area 4 start', ParamType.Text),
        'areaendo1': ParamInfoRW('Ouput area 1 end', ParamType.Text),
        'areaendo2': ParamInfoRW('Ouput area 2 end', ParamType.Text),
        'areaendo3': ParamInfoRW('Ouput area 3 end', ParamType.Text),
        'areaendo4': ParamInfoRW('Ouput area 4 end', ParamType.Text),
        'noofareasc': ParamInfoRW('Number of areas for confirmation', ParamType.Text),
        'areastartc1': ParamInfoRW('Area for confirmation 1 start', ParamType.Text),
        'areastartc2': ParamInfoRW('Area for confirmation 2 start', ParamType.Text),
        'areastartc3': ParamInfoRW('Area for confirmation 3 start', ParamType.Text),
        'areastartc4': ParamInfoRW('Area for confirmation 4 start', ParamType.Text),
        'areaendc1': ParamInfoRW('Area for confirmation 1 end', ParamType.Text),
        'areaendc2': ParamInfoRW('Area for confirmation 2 end', ParamType.Text),
        'areaendc3': ParamInfoRW('Area for confirmation 3 end', ParamType.Text),
        'areaendc4': ParamInfoRW('Area for confirmation 4 end', ParamType.Text),
        'sensortype': ParamInfoRW('Sensor type', ParamType.Text),
        'firmware': ParamInfoRW('Firmware version', ParamType.Text),
        'option': ParamInfoRW('Option list', ParamType.Text),
        'noofpixels': ParamInfoRW('Number of pixels', ParamType.Text),
        'clockfrequency': ParamInfoRW('Clock frequency', ParamType.Text),
        'bitdepth': ParamInfoRW('Bit depth', ParamType.Text),
        'twopcthreshold': ParamInfoRW('Use two thresholds instead of one', ParamType.Text),
        'automaticbundleheight': ParamInfoRW('Use automatic calculation of bundle height', ParamType.Text),
        'genericcamtrigger': ParamInfoRW('Programming of the Trigger', ParamType.Text),
        'intervaltime': ParamInfoRW('Programming of the Interval Time', ParamType.Text),
        'pulsewidth': ParamInfoRW('Programming of the Interval Time', ParamType.Text),
        'serialin': ParamInfoRW('Programming of the Serial In string', ParamType.Text),
        'serialout': ParamInfoRW('Programming of the Serial Out string', ParamType.Text),
        'camerainfo': ParamInfoRW('Camera info text', ParamType.Text),
    },
    'acquire': {
        'exposure': ParamInfoRW('Exposure time', ParamType.Text),
        'gain': ParamInfoRW('Analog gain', ParamType.Text),
        'offset': ParamInfoRW('Analog Offset', ParamType.Text),
        'nrtrigger': ParamInfoRW('Number of trigger', ParamType.Text),
        'threshold': ParamInfoRW('Photon counting threshold', ParamType.Text),
        'threshold2': ParamInfoRW('Second photon counting threshold', ParamType.Text),
        'dortbacksub': ParamInfoRW('Do realtime background subtraction', ParamType.Text),
        'dortshading': ParamInfoRW('Do realtime shading correction', ParamType.Text),
        'nrexposures': ParamInfoRW('Number of exposures', ParamType.Text),
        'clearframebuffer': ParamInfoRW('Clear frame buffer on start', ParamType.Text),
        'ampgain': ParamInfoRW('Amp gain', ParamType.Text),
        'smd': ParamInfoRW('Scan mode', ParamType.Text),
        'recurnumber': ParamInfoRW('Recursive filter', ParamType.Text),
        'hvoltage': ParamInfoRW('High Voltage', ParamType.Text),
        'amd': ParamInfoRW('Acquire mode', ParamType.Text),
        'ash': ParamInfoRW('Acquire shutter', ParamType.Text),
        'atp': ParamInfoRW('Acquire trigger polarity', ParamType.Text),
        'sop': ParamInfoRW('Scan optical black', ParamType.Text),
        'spx': ParamInfoRW('Superpixel', ParamType.Text),
        'mcp': ParamInfoRW('MCP gain', ParamType.Text),
        'tdy': ParamInfoRW('Time delay', ParamType.Text),
        'integraftertrig': ParamInfoRW('Integrate after trigger', ParamType.Text),
        'sensitivityvalue': ParamInfoRW('Sensitivity (value)', ParamType.Text),
        'emg': ParamInfoRW('EM-gain (EM-CCD camera)', ParamType.Text),
        'bgsub': ParamInfoRW('Background Sub', ParamType.Text),
        'recurfilter': ParamInfoRW('Recursive Filter', ParamType.Text),
        'highvoltage': ParamInfoRW('High Voltage', ParamType.Text),
        'streaktrigger': ParamInfoRW('Streak trigger', ParamType.Text),
        'fgtrigger': ParamInfoRW('Frame grabber Trigger', ParamType.Text),
        'sensitivityswitch': ParamInfoRW('Sensitivity (switch)', ParamType.Text),
        'bgoffset': ParamInfoRW('Background offset', ParamType.Text),
        'atn': ParamInfoRW('Acquire trigger number', ParamType.Text),
        'smdextended': ParamInfoRW('Scan mode extended', ParamType.Text),
        'lightmode': ParamInfoRW('Light mode', ParamType.Text),
        'scanspeed': ParamInfoRW('Scan Speed', ParamType.Text),
        'bgdatamemory': ParamInfoRW('Memory number for background data (Inbuilt background sub)', ParamType.Text),
        'shdatamemory': ParamInfoRW('Memory number for shading data (Inbuilt shading correction)', ParamType.Text),
        'sensitivitymode': ParamInfoRW('Sensitivity mode', ParamType.Text),
        'sensitivity': ParamInfoRW('Sensitivity', ParamType.Text),
        'sensitivity2mode': ParamInfoRW('Sensitivity 2 mode', ParamType.Text),
        'sensitivity2': ParamInfoRW('Sensitivity 2', ParamType.Text),
        'contrastcontrol': ParamInfoRW('Contrast control', ParamType.Text),
        'contrastgain': ParamInfoRW('Contrast gain', ParamType.Text),
        'contrastoffset': ParamInfoRW('Contrast offset', ParamType.Text),
        'photonimagingmode': ParamInfoRW('Photon Imaging mode', ParamType.Text),
        'highdynamicrangemode': ParamInfoRW('High dynamic range mode', ParamType.Text),
        'recurnumber2': ParamInfoRW('Second number for recursive filter', ParamType.Text),
        'recurfilter2': ParamInfoRW('Second recursive filter', ParamType.Text),
        'frameavgnumber': ParamInfoRW('Frame average number', ParamType.Text),
        'frameavg': ParamInfoRW('Frame average', ParamType.Text),
    },
    'live': {},
    'ai': {},
    'pc': {},
}


def parse_config_params(config, group):
    config.update({k.lower(): config[k] for k in config})
    result = {}
    for k, v in group.items():
        info = group[k]
        value = config.get(k.lower(), info.default)
        result[k] = Param(value, info)
    return result


class RemoteEx(BaseDevice):
    """
    Hamamatsu RemoteEx interface
    """

    DEFAULT_NEWLINE = b'\r'

    def __init__(self, name, **opts):
        super(RemoteEx, self).__init__(name)
        self._app = parse_config_params(opts.get('application', {}), AppParamInfo)
        self._main = parse_config_params(opts.get('main', {}), MainParamInfo)
        self._acq = parse_config_params(opts.get('acquisition', {}), AcqParamInfo)
        self._cam = {}
        cam_opts = opts.get('camera', {})
        for gname, gparaminfo in CamParamInfo.items():
            gopts = cam_opts.get(gname, {})
            self._cam[gname] = parse_config_params(gopts, gparaminfo)
        self._status = 'idle'
        self._started = False
        self._acq_mode = None
        self._commands = cmds = {}
        data_url = opts.get('data_url')
        if data_url:
            self._data_stream = StreamServer(data_url, self.handle_data_connection)
            self._data_stream.start()
        for name in dir(self):
            cmd = getattr(self, name)
            if callable(cmd) and hasattr(cmd, 'command'):
                cmds[cmd.command['name']] = cmd

    def handle_data_connection(self, sock, addr):
        info = self._log.info
        info("new connection to data port from %s", addr)
        i = 0
        try:
            while True:
                gevent.sleep(1)
                sock.sendall(b'binary data %d\n' %i)
                i += 1
        except:
            pass
        info("client disconnected from data port %s", addr)

    def on_connection(self, transport, conn):
        conn.sendall(b'RemoteEx Ready\r')

    def handle_line(self, line):
        self._log.debug("processing line %r", line)
        line = line.decode().strip()
        match = cmd_re.match(line)
        if match is None:
            result = InvalidSyntax(line)
        else:
            cmd_info = match.groupdict()
            cmd_name, args = cmd_info['name'], cmd_info['args']
            cmd = self._commands.get(cmd_name.lower())
            if cmd is None:
                result = UnknownCommandOrParameters(cmd_name)
            else:
                args = args.split(',') if args else []
                result = cmd(*args)
                if result is None:
                    result = Ok(*args)
        if isinstance(result, Ok):
            result.args.insert(0, cmd_name)
        reply = str(result).encode() + b'\r'
        self._log.debug('replying with %r', reply)
        return reply

    @command
    def app_info(self, param):
        try:
            return Ok(self._app[param.lower()].value)
        except KeyError:
            return UnknownCommandOrParameters('appinfo')

    @command
    def status(self):
        return Ok(self._status)

    @command
    def stop(self):
        pass

    @command
    def shutdown(self):
        pass

    @command
    def app_start(self, visible='1', ini_file=None):
        self._started = True

    @command
    def app_end(self):
        self._started = False
        self._acq_mode = None

    @command
    def main_param_get(self, pname):
        try:
            param = self._main[pname.lower()]
            encode = param.info.type.value[0]
            return Ok(encode(param.value))
        except KeyError:
            return UnknownCommandOrParameters('mainparamget')

    @command
    def acq_start(self, mode):
#        if self._acq_mode:
#            return CommandNotPossible()
        try:
            self._acq_mode = AcqMode[mode]
            return Ok()
        except KeyError:
            return UnknownCommandOrParameters('acqstart')

    @command
    def acq_status(self):
        if self._acq_mode:
            return Ok('busy', self._acq_mode.name)
        return Ok('idle')

    @command
    def acq_stop(self, timeout='1000'):
        self._acq_mode = None

    @command
    def acq_param_get(self, pname):
        try:
            param = self._acq[pname.lower()]
            encode = param.info.type.value[0]
            return Ok(encode(param.value))
        except KeyError:
            return UnknownCommandOrParameters('acqparamget')

    @command
    def acq_param_set(self, pname, value):
        try:
            param = self._acq[pname.lower()]
            decode = param.info.type.value[1]
            param.value = decode(value)
            return Ok()
        except KeyError:
            return UnknownCommandOrParameters('acqparamset')

    @command
    def acq_param_info(self, pname):
        raise NotImplementedError

    @command
    def acq_live_monitor(self, monitor_type, *args):
        return Ok()

    @command
    def acq_live_monitor_ts_info(self):
        ts = time.time()
        dt = datetime.datetime.fromtimestamp(ts).strftime('%H:%M:%S')
        return Ok(dt, '{:.3f}'.format(ts))

    @command
    def acq_live_monitor_ts_format(self, fmt):
        raise NotImplementedError

    @command
    def cam_param_get(self, lname, pname):
        try:
            location = self._cam[lname.lower()]
            param = location[pname.lower()]
            encode = param.info.type.value[0]
            return Ok(encode(param.value))
        except KeyError:
            return UnknownCommandOrParameters('camparamget')

    @command
    def cam_param_set(self, lname, pname, value):
        try:
            location = self._cam[lname.lower()]
            param = location[pname.lower()]
            decode = param.info.type.value[1]
            param.value = decode(value)
            Ok()
        except KeyError:
            return UnknownCommandOrParameters('camparamset')

    @command
    def cam_param_info(self, pname):
        raise NotImplementedError

    @command
    def cam_get_live_bg(self):
#        if self._acq_mode != AcqMode.Live:
#            return CommandNotPossible()
        return Ok()
