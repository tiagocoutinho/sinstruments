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

Complex configuration with default values on simulator startup:

- class: CryoCon
  module: cryocon
  transports:
  - type: tcp
    url: :5001
  channels:
  - name: A
    unit: K
  - name: B
    unit: K
  loops:
  - source: A
    type: MAN
  system distc: 4
  system lockout: OFF
  system remled: ON
  control: OFF
"""

import time
import random
import functools

import gevent

from sinstruments.simulator import BaseDevice


DEFAULT_CHANNEL = {
    'unit': 'K'
}

DEFAULT_LOOP = {
    'source': 'A',
    'type': 'MAN',
    'outpwr': '40.3',
}

DEFAULT = {
    'system lockout': False,
    'system distc': 1,
    'system remled': True,
    'control': False,
    'channels': [dict(DEFAULT_CHANNEL, name=name) for name in 'ABCD'],
    'loops': [dict(DEFAULT_LOOP) for loop in range(2)]
}


class CryoCon(BaseDevice):

    MIN_TIME = 0.1

    def __init__(self, name, **opts):
        kwargs = {}
        if 'newline' in opts:
            kwargs['newline'] = opts.pop('newline')
        self._config = dict(DEFAULT, **opts)
        self._config['channels'] = [dict(DEFAULT_CHANNEL, **channel)
                                    for channel in self._config['channels']]
        self._config['loops'] = [dict(DEFAULT_LOOP, **loop)
                                 for loop in self._config['loops']]
        super().__init__(name, **kwargs)
        self._last_request = 0

    def handle_line(self, line):
        self._log.debug('request %r', line)
        curr_time = time.time()
        dt = self.MIN_TIME - (curr_time - self._last_request)
        self._last_request = curr_time
        if dt > 0:
            self._log.debug('too short requests. waiting %f ms', dt*1000)
            gevent.sleep(dt)
        line = line.strip().decode()
        cmds = (cmd for cmd in line.split(';'))
        cmds = [cmd.upper() for cmd in cmds if cmd]
        results = []
        for cmd in cmds:
            if cmd[0] == ':':
                cmd = cmd[1:]
            result = self.handle_command(cmd)
            if result is not None:
                results.append(result)
        if results:
            result = ';'.join(results) + '\n'
            result = result.encode()
            self._log.debug('reply %r', result)
            return result

    def handle_command(self, command):
        self._log.debug('handle command %r', command)
        query = '?' in command
        args = command.split(' ', 1)
        cmd = args[0]
        cmd_line = args[1] if len(args) > 1 else ''
        if cmd.startswith('INPUT'):
            if 'UNIT' in cmd_line:
                if query:
                    return 'K'
            else:
                if query:
                    args = cmd_line.split(',')
                    temps = dict(A='11.456', B='12.456', C='13.456', D='14.456')
                    values = [temps[arg] for arg in args]
                    return ';'.join(values)
        elif cmd.startswith('LOOP'):
            if 'SOURCE' in cmd_line:
                if query:
                    return 'A'
            elif 'TYPE' in cmd_line:
                if query:
                    return 'MAN'
            elif 'OUTPWR' in cmd_line:
                if query:
                    return '40.3'
            elif 'RANGE' in cmd_line:
                if query:
                    return 'MID'
            elif 'RATE' in cmd_line:
                if query:
                    return '71'
            elif 'SETPT' in cmd_line:
                if query:
                    return '16.34'
        elif cmd.startswith('SYSTEM'):
            if 'DISTC' in cmd:
                if query:
                    return '1'
            elif 'LOCKOUT' in cmd:
                if query:
                    return 'ON'
            elif 'REMLED' in cmd:
                if query:
                    return 'ON'
        elif cmd.startswith('CONTROL'):
            if query:
                return 'ON'
