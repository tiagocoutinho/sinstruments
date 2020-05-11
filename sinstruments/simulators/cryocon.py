# -*- coding: utf-8 -*-
#
# This file is part of the instrument simulator project
#
# Copyright (c) 2019 Tiago Coutinho
# Distributed under the MIT. See LICENSE for more info.

"""
.. code-block:: yaml

    devices:
    - class: CryoCon
      module: cryocon
      transports:
      - type: tcp
        url: :5000

A simple *nc* client can be used to connect to the instrument:

    $ nc 0 5000
    *IDN?
    Cryo-con,24C,204683,1.01A

Complex configuration with default values on simulator startup:

- class: CryoCon
  module: cryocon
  transports:
  - type: tcp
    url: :5001
  channels:
  - id: A
    unit: K
  - id: B
    unit: K
  loops:
  - id: 1
    source: A
    type: MAN
  distc: 4
  lockout: OFF
  remled: ON
  control: OFF
"""

import time
import random
import functools

import scpi
import gevent

from sinstruments.simulator import BaseDevice


DEFAULT_CHANNEL = {
    'unit': 'K',
}

def Channel(id, data):
    channel = dict(DEFAULT_CHANNEL, name='Channel'+id)
    channel.update(data)
    return channel


TEMPS = ['11.456', '12.456', '13.456', '14.456', '.......']

DEFAULT_LOOP = {
    'source': 'A',
    'type': 'MAN',
    'output power': '40.3',
    'setpoint': '0.0',
    'rate': '10.0',
    'range': '1.0',
}

def Loop(id, data):
    return dict(DEFAULT_LOOP, **data)

DEFAULT = {
    '*idn': 'Cryo-con,24C,204683,1.01A',
    'lockout': 'OFF',
    'distc': 1,
    'remled': 'OFF',
    'control': 'OFF',
    'channels': {name: Channel(name, {}) for name in 'ABCD'},
    'loops': {str(loop): Loop(loop, {}) for loop in range(2)}
}


class CryoCon(BaseDevice):

    MIN_TIME = 0.1

    def __init__(self, name, **opts):
        kwargs = {}
        if 'newline' in opts:
            kwargs['newline'] = opts.pop('newline')
        self._config = dict(DEFAULT, **opts)
        self._config['channels'] = {channel['id'].upper(): Channel(channel['id'], channel)
                                    for channel in self._config['channels']}
        self._config['loops'] = {str(loop['id']): Loop(loop['id'], loop)
                                 for loop in self._config['loops']}
        super().__init__(name, **kwargs)
        self._last_request = 0
        self._cmds = scpi.Commands({
            '*IDN': scpi.Cmd(get=lambda req: self._config['*idn']),
            'SYSTem:LOCKout': scpi.Cmd(get=self.lockout, set=self.lockout),
            'SYSTem:REMLed': scpi.Cmd(get=self.remled, set=self.remled),
            'CONTrol': scpi.Cmd(get=self.control, set=self.control),
            'STOP': scpi.Cmd(set=self.stop),
            'INPut': scpi.Cmd(get=self.get_input, set=self.set_input),
            'LOOP': scpi.Cmd(get=self.get_loop, set=self.set_loop),
        })

    def handle_line(self, line):
        self._log.debug('request %r', line)
        curr_time = time.time()
        dt = self.MIN_TIME - (curr_time - self._last_request)
        self._last_request = curr_time
        if dt > 0:
            self._log.debug('too short requests. waiting %f ms', dt*1000)
            gevent.sleep(dt)
        line = line.decode()
        requests = scpi.requests(line)
        results = (self.handle_request(request) for request in requests)
        results = (result for result in results if result is not None)
        reply = ';'.join(results).encode()
        if reply:
            reply += b'\n'
            self._log.debug('reply %r', reply)
            return reply

    def handle_request(self, request):
        cmd = self._cmds.get(request.name)
        if cmd is None:
            return 'NAK'
        if request.query:
            getter = cmd.get('get')
            if getter is None:
                return 'NAK'
            return cmd['get'](request)
        else:
            setter = cmd.get('set')
            if setter is None:
                return 'NAK'
            return cmd['set'](request)

    def lockout(self, request):
        if request.query:
            return 'ON' if self._config['lockout'] in ('ON', True) else 'OFF'
        args = request.args.upper()
        if args in ('ON', 'OFF'):
            self._config['lockout'] = args

    def remled(self, request):
        if request.query:
            return 'ON' if self._config['remled'] in ('ON', True) else 'OFF'
        args = request.args.upper()
        if args in ('ON', 'OFF'):
            self._config['remled'] = args

    def control(self, request):
        if request.query:
            return 'ON' if self._config['control'] in ('ON', True) else 'OFF'
        self._config['control'] = 'ON'

    def stop(self, request):
        self._config['control'] = 'OFF'

    def get_input(self, request):
        if ':' in request.args:
            channels, variable = request.args.split(':', 1)
            variable = variable.upper()
        else:
            channels, variable = request.args, 'TEMP'
        channels = [ch.upper() for ch in channels.split(',')]
        if variable.startswith('TEMP'):
            values = [random.choice(TEMPS) for channel in channels]
            return ';'.join(values)
        elif variable.startswith('UNIT'):
            ch = self._config['channels']
            values = [ch[channel]['unit'] for channel in channels]
            return ';'.join(values)
        elif variable.startswith('NAME'):
            ch = self._config['channels']
            values = [ch[channel]['name'] for channel in channels]
            return ';'.join(values)
        else:
            return 'NAK'

    def set_input(self, request):
        arg, value = request.args.split(' ', 1)
        channel, variable = arg.split(':', 1)
        variable = variable.upper()
        channels = self._config['channels']
        channel = channels[channel.upper()]
        if variable.startswith('UNIT'):
            channel['unit'] = value
        elif variable.startswith('NAME'):
            channel['name'] = value
        else:
            return 'NAK'

    def get_loop(self, request):
        channel, variable = request.args.split(':', 1)
        variable = variable.upper()
        loop = self._config['loops']
        channel = loop[channel]
        if variable.startswith('SOUR'):
            return channel['source']
        elif variable.startswith('SETP'):
            return channel['setpoint']
        elif variable.startswith('TYP'):
            return channel['type']
        elif variable.startswith('OUTP'):
            return channel['output power']
        elif variable.startswith('RAT'):
            return channel['rate']
        elif variable.startswith('RANG'):
            return channel['range']
        else:
            return 'NAK'

    def set_loop(self, request):
        arg, value = request.args.split(' ', 1)
        channel, variable = arg.split(':', 1)
        variable = variable.upper()
        loop = self._config['loops']
        channel = loop[channel]
        if variable.startswith('SOUR'):
            channel['source'] = value
        elif variable.startswith('SETP'):
            channel['setpoint'] = value
        elif variable.startswith('TYP'):
            channel['type'] = value
        elif variable.startswith('OUTP'):
            channel['output power'] = value
        elif variable.startswith('RAT'):
            channel['rate'] = value
        elif variable.startswith('RANG'):
            channel['range'] = value
        else:
            return 'NAK'
