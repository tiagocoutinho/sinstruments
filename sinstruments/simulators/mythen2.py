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
      module: mythen2
      transports:
      - type: udp
        url: :1030
      - type: tcp
        url: :1031

A simple *nc* client can be used to connect to the instrument:

    $ nc 0 1031

"""

import time
import struct
import collections

import gevent.queue

from sinstruments.simulator import BaseDevice, MessageProtocol


Type = collections.namedtuple("Type", "name encode decode default")


def _Type(name, decoder=int, type="i", default=None):
    def encode(ctx):
        return struct.pack("<{}".format(type), ctx[name])
    def decode(ctx, value):
        ctx[name] = value = decoder(value)
        return value
    return Type(name, encode, decode, default)


def Str(name, default=None):
    def encode(ctx):
        return ctx[name].encode()
    def decode(ctx):
        ctx[name] = value = value.decode()
        return value
    return Type(name, encode, decode, default)


def Int(name, default=None):
    return _Type(name, int, "i", default=default)


def Long(name, default=None):
    return _Type(name, int, "q", default=default)


def Float(name, default=None):
    return _Type(name, float, "f", default=default)


def _TypeArrayNMod(name, decoder=int, type="i", default=None):
    def encode(ctx):
        n, v = ctx["nmodules"], ctx[name]
        return struct.pack("<{}{}".format(n, type), *v[:n])
    def decode(ctx, value):
        v = [decoder(i) for i in value]
    return Type(name, encode, decode, default)


def IntArrayNMod(name, default=None):
    return _TypeArrayNMod(name, int, "i", default=default)


def FloatArrayNMod(name, default=None):
    return _TypeArrayNMod(name, float, "f", default=default)


def _TypeArrayNChan(name, decoder=int, type="i", default=None):
    def encode(ctx):
        nb_mod, nb_ch, v = ctx["nmodules"], ctx['modchannels'], ctx[name]
        n = nb_mod * nb_ch
        return struct.pack("<{}{}".format(n, type), *v[:n])
    def decode(ctx, value):
        v = [decoder(i) for i in value]
    return Type(name, encode, decode, default)


def IntArrayNChan(name, default=None):
    return _TypeArrayNChan(name, int, "i", default=default)


TYPES = (
    Int("nmodules", 4),
    Int("nmaxmodules", 4),
    Int("module", 65535),
    Int("modchannels", 1280),
    Str("version", "M4.0.1\x00"),
    Long("time", 10_000_000),
    Int("nbits", 24),
    Int("frames", 1),
    Int("conttrigen", 0),
    Int("gateen", 0),
    Int("delafter", 0),
    Int("trigen", 0),
    Int("inpol", 0),   # 0 - rising edge, 1 - falling edge (removed in v4.0)
    Int("outpol", 0),  # 0 - rising edge, 1 - falling edge (removed in v4.0)
    Int("badchannelinterpolation", 0),
    Int("flatfieldcorrection", 0),
    Int("ratecorrection", 0),
    IntArrayNMod("settings", 4*[0]),   # 0: Standard, 1: Highgain, 2: Fast, 3: Unknown (deprecated since v4.0)
    Str("settingsmode", "auto 5600 11200"),
    FloatArrayNMod("tau", [4.6, 8.7, 7.4, 2.1]),
    FloatArrayNMod("kthresh", 4*[6.4]),
    FloatArrayNMod("kthreshmin", 4*[0.05]),
    FloatArrayNMod("kthreshmax", 4*[69.56]),
    FloatArrayNMod("energy", 4*[8.05]),
    FloatArrayNMod("energymin", 4*[0.05]),
    FloatArrayNMod("energymax", 4*[69.56]),
    IntArrayNChan("badchannels", 4*1280*[0]),
)

TYPE_MAP = {t.name: t for t in TYPES}


OK = 4*b"\x00"


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
        total_nb_frames = self.nb_frames
        exp_time = self.exposure_time
        nb_channels = self.nb_channels
        buff = self.buffer
        start = time.monotonic()
        while frame_nb < total_nb_frames:
            wake_up_at = (frame_nb + 1) * exp_time + start
            sleep_for = wake_up_at - time.monotonic()
            if sleep_for > 0:
                gevent.sleep(sleep_for)
            data = nb_channels * struct.pack("<i", frame_nb)
            buff.put(data)
            frame_nb += 1


def start_acquisition(config, nb_frames=None):
    nb_frames = config["frames"] if nb_frames is None else nb_frames
    exp_time = config["time"] * 1E-7
    nb_channels = config["nmodules"] * config["modchannels"]
    acq = Acquisition(nb_frames, exp_time, nb_channels)
    acq_task = gevent.spawn(acq.run)
    acq_task.acquisition = acq
    return acq_task


class Mythen2(BaseDevice):

    protocol = Protocol

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = {name: t.default for name, t in TYPE_MAP.items()}
        self.config.update(self.props)
        self.start_acquisition(0) # start dummy acquisition

    def start_acquisition(self, nb_frames=None):
        self.acq_task = start_acquisition(self.config, nb_frames=nb_frames)
        return self.acq_task

    @property
    def acq(self):
        return self.acq_task.acquisition

    def status(self):
        running = 0 if self.acq_task.ready() else 1
        readout = 1<<16 if self.acq.buffer.empty() else 0
        return struct.pack("<i", running | readout)

    def handle_message(self, message):
        self._log.info("handling: %r", message)
        result = self._handle_message(message)
        if result is None:
            self._log.info("return: None")
        else:
            size = len(result)
            if size > 40:
                self._log.info("return (%d): %s [...] %s", len(result),
                               result[:10], result[-10:])
            else:
                self._log.info("return (%d): %r", len(result), result)
        return result

    def __getitem__(self, name):
        return TYPE_MAP[name].encode(self.config)

    def __setitem__(self, name, value):
        TYPE_MAP[name].decode(self.config, value)

    def _handle_message(self, message):
        message = message.strip().decode()
        assert message[0] == "-"
        cmd, *data = message.split(" ", 1)
        cmd = cmd[1:]
        if cmd == "get":
            assert len(data) == 1
            data = data[0]
            if data == "status":
                return self.status()
            else:
                return self[data]
        elif cmd == "reset":
            gevent.sleep(2 + 0.5 * self.config["nmodules"])
            return OK
        elif cmd == "start":
            self.start_acquisition()
            return OK
        elif cmd == "stop":
            self.acq_task.kill()
            return OK
        elif cmd == "readout":
            nb_frames = int(data[0]) if data else 1
            while nb_frames > 0:
                return self.acq.buffer.get()
        else:
            assert len(data) == 1
            self[cmd] = data[0]
            return OK
