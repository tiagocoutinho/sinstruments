# -*- coding: utf-8 -*-
#
# This file is part of the instrument simulator project
#
# Copyright (c) 2020 Tiago Coutinho
# Distributed under the MIT. See LICENSE for more info.

"""
.. code-block:: yaml

    devices:
        - class: Mythen2
          module: mythen
          transports:
              - type: tcp
                url: :1031

A simple *nc* client can be used to connect to the instrument:

    $ nc 0 1031

"""

import struct
import collections

import gevent.queue

from sinstruments.simulator import BaseDevice, MessageProtocol


#Int = lambda i: struct.pack("<i", i)

Type = collections.namedtuple('Type', 'get set value')

def _Type(v, type='<i'):
    def get(ctx):
        return struct.pack(type, v)
    def value():
        return v
    def set(ctx, new_v):
        nonlocal v
        v = int(new_v)
    return Type(get, set, value)


def Int(v):
    return _Type(v, "<i")


def Long(v):
    return _Type(v, "<q")


def Float(v):
    return _Type(v, "<f")


def IntArrayNMod(v):
    def get(ctx):
        n = ctx['nmodules'].value()
        return struct.pack(f"<{n}i", *v[:n])
    def value():
        return v
    def set(ctx, new_v):
        nonlocal v
        v = [int(i) for i in new_v]
    return Type(get, set, value)


def FloatArrayNMod(v):
    def get(ctx):
        n = ctx['nmodules'].value()
        return struct.pack(f"<{n}f", *v[:n])
    def value():
        return v
    def set(ctx, new_v):
        nonlocal v
        v = [float(i) for i in new_v]
    return Type(get, set, value)


DEFAULTS = {
    "nmodules": Int(4),
    "nmaxmodules": Int(4),
    "module": Int(65535),
    "modchannels": Int(1280),
#    "modnum": lambda ctx: [12345+i for i in range(ctx["nmodules"])],
    "version": b"M4.0.1\x00",
    "time": Long(10_000_000),
    "nbits": Int(24),
    "frames": Int(1),
    "conttrigen": Int(0),
    "gateen": Int(0),
    "delafter": Int(0),
    "trigen": Int(0),
    "inpol": Int(0),   # 0 - rising edge, 1 - falling edge (removed in v4.0)
    "outpol": Int(0),  # 0 - rising edge, 1 - falling edge (removed in v4.0)
    "badchannelinterpolation": Int(0),
    "flatfieldcorrection": Int(0),
    "ratecorrection": Int(0),
    "settings": IntArrayNMod([0, 0, 0, 0]),   # 0: Standard, 1: Highgain, 2: Fast, 3: Unknown (deprecated since v4.0)
    "settingsmode": b"auto 5600 11200",
    "tau": FloatArrayNMod([4.6, 8.7, 7.4, 2.1]),
    "kthresh": FloatArrayNMod([8.05, 8.05, 8.05, 8.05]),
}


OK = 4*b'\x00'


class Protocol(MessageProtocol):

    def read_messages(self):
        transport = self.transport
        while True:
            data = transport.read(self.channel, size=4096)
            if not data:
                return
            yield data


class Acquisition:

    def __init__(self, nb_frames, exposure_time, nb_channels):
        self.nb_frames = nb_frames
        self.exposure_time = exposure_time
        self.nb_channels = nb_channels
        self.finished = None
        self.buffer = gevent.queue.Queue()

    def run(self):
        self.finished = False
        try:
            self._run()
        finally:
            self.finished = True

    def _run(self):
        frame_nb = 0
        while frame_nb < self.nb_frames:
            gevent.sleep(self.exposure_time)
            data = self.nb_channels * struct.pack('<i', frame_nb)
            self.buffer.put(data)
            frame_nb += 1


class Mythen2(BaseDevice):

    protocol = Protocol

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = dict(DEFAULTS, **self.props)
        self.acq = None

    def status(self):
        acq = self.acq.acquisition if self.acq else None
        running = 0b0 if acq is None or acq.finished else 0b1
        readout = 0b0 if acq is None or acq.buffer.empty() else 1 << 16
        return struct.pack("<i", running & readout)

    def handle_message(self, message):
        self._log.info("handling: %r", message)
        result = self._handle_message(message)
        self._log.info("return: %r", "None" if result is None else result[:40])
        return result

    def __getitem__(self, name):
        param = self.config[name]
        return param.get(self.config) if isinstance(param, Type) else param

    def __setitem__(self, name, value):
        self.config[name].set(self.config, value)

    def _handle_message(self, message):
        message = message.strip().decode()
        assert message[0] == "-"
        cmd, *data = message.split(" ", 1)
        cmd = cmd[1:]
        if cmd == "get":
            assert len(data) == 1
            data = data[0]
            if data == 'status':
                return self.status()
            else:
                return self[data]
        elif cmd == "reset":
            gevent.sleep(2 + 0.5 * self.config["nmodules"].value())
            return OK
        elif cmd == "start":
            nb_frames = self.config['frames'].value()
            exp_time = self.config['time'].value() * 1E-7
            nb_channels = self.config["nmodules"].value() * self.config["modchannels"].value()
            acq = Acquisition(nb_frames, exp_time, nb_channels)
            self.acq = gevent.spawn(acq.run)
            self.acq.acquisition = acq
            return OK
        elif cmd == "readout":
            nb_frames = int(data[0]) if data else 1
            while nb_frames > 0:
                return self.acq.acquisition.buffer.get()
        else:
            assert len(data) == 1
            self[cmd] = data[0]
            return OK
