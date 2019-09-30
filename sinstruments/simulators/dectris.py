# -*- coding: utf-8 -*-
#
# This file is part of the instrument simulator project
#
# Copyright (c) 2018 Tiago Coutinho
# Distributed under the MIT. See LICENSE for more info.

"""
Dectris Pilatus 2 and 3 simulator

To create a Pilatus device use the following configuration as
a starting point:

.. code-block:: yaml

    devices:
    - class: Pilatus
      module: dectris
      transports:
      - type: tcp
        url: :41234

A simple *nc* client can be used to connect to the instrument:

    $ nc 0 41234

"""

import inspect
import logging
import pathlib
import datetime

import gevent

from sinstruments.simulator import BaseDevice

NL = b'\x18'
OK = 'OK'
CMD_START = 15
CMD_END = 7

SETENERGY_REPLY = """\
15 OK Energy setting: {energy} eV
 Settings: {gain} gain; threshold: {threshold} eV; vcmp: {vcmp} V
 Trim file:
  {trim_file}"""

def unrecognized(cmd, args, settings):
    return '1 ERR *** Unrecognized command: {}'.format(cmd)

def version(cmd, args, settings):
    return '24 OK Code release:  {version}'.format_map(settings)

def setenergy(cmd, args, settings):
    return SETENERGY_REPLY.format_map(settings)

def setthreshold(cmd, args, settings):
    if args:
        if len(args) == 2:
            settings['gain'] = args[0][:-1]
            settings['threshold'] = float(args[1])
        else:
            settins['threshold'] = float(args[0])
    return SETENERGY_REPLY.format_map(settings)

def exptime(cmd, args, settings):
    if args:
        settings['exptime'] = float(args[0])
    return '15 OK Exposure time set to: {exptime} sec.'.format_map(settings)

def expperiod(cmd, args, settings):
    if args:
        settings['expperiod'] = float(args[0])
    return '15 OK Exposure period set to: {expperiod} sec.'.format_map(settings)

def nimages(cmd, args, settings):
    if args:
        settings['nimages'] = int(args[0])
    return '15 OK N images set to: {nimages}'.format_map(settings)

def setroi(cmd, args, settings):
    if args:
        settings['roi'] = int(args[0])
    return '15 OK Readout pattern is ' + 'full detector' if settings['roi'] == 0 else '?'

def imgpath(cmd, args, settings):
    if args:
        settings['imgpath'] = pathlib.Path(args[0])
    return '10 OK {imgpath}'.format_map(settings)

def delay(cmd, args, settings):
    if args:
        settings['delay'] = float(args[0])
    return '15 OK Delay time set to: {delay}'.format_map(settings)

def nexpframe(cmd, args, settings):
    if args:
        settings['nexpframe'] = int(args[0])
    return '15 OK Exposures per frame set to: {nexpframe}'.format_map(settings)

def setackint(cmd, args, settings):
    if args:
        settings['setackint'] = int(args[0])
    return '15 OK Acknowledgment interval is {setackint}'.format_map(settings)

def gapfill(cmd, args, settings):
    if args:
        settings['gapfill'] = int(args[0])
    return '15 OK Detector gap-fill is: {gapfill}'.format_map(settings)

def dbglvl(cmd, args, settings):
    if args:
        settings['dbglvl'] = int(args[0])

def exposure(cmd, args, settings):
    name = pathlib.Path(args[0])
    full_name = name if name.is_absolute() else settings['imgpath'] / name
    nimages = settings['nimages']
    if nimages > 1:
        names = (full_name.with_name('{p.stem}{i}{p.suffix}'.format(i=i, p=full_name))
                 for i in range(nimages))
    else:
        names = full_name,
    t = datetime.datetime.now()
    yield '15 OK  Starting {} second background: {:%Y-%b-%dT%H:%M:%S.%f}'.format(
        settings['expperiod'], datetime.datetime.now())[:-3]
    for name in names:
        logging.info('starting acquisition of %r', name)
        gevent.sleep(settings['expperiod'])
        logging.info('finished acquisition of %r', name)
    yield '7 OK {}'.format(name)


def k():
    pass


def resetcam(cmd, args, settings):
    return '15 OK'


class Pilatus(BaseDevice):

    DEFAULT_NEWLINE = NL

    SETTINGS = dict(
        energy=12600,
        gain='low',
        gapfill=0,
        threshold=6511,
        vcmp=0.789,
        roi=0,
        exptime=1.0,
        expperiod=1.05,
        nimages=1,
        nexpframe=1,
        delay=0,
        setackint=0,
        dbglvl=1,
        exposure='',
        imgpath=pathlib.Path('/home/det/p2_det/images'),
        trim_file='/home/det/p2_det/config/calibration/p6m0108_E13022_T6511_vrf_m0p30.bin',
        version='tvx-7.3.13-121212')

    def __init__(self, name, **kwargs):
        settings = kwargs.pop('settings', {})
        super(Pilatus, self).__init__(name, **kwargs)
        self.settings = dict(self.SETTINGS)
        self.settings.update(settings)

    def handle_line(self, line):
        self._log.debug("processing line %r", line)
        line = line.strip().decode()
        cmd, *args = line.split()
        func = globals().get(cmd, unrecognized)
        reply = func(cmd, args, self.settings)
        self._log.debug("finished processing line %r: %r", line, reply)
        if reply is None:
            return
        if not inspect.isgenerator(reply):
            reply = reply,
        for response in reply:
            yield response.encode() + NL

