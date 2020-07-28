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

A simple *nc* client can be used to connect to the instrument (`-I 1` disables
the input buffer since the protocol replies are a binary without terminator)::

    $ nc -I 1 0 1031
    -get version


External signal to trigger/gate mode can be simulated by configuring
an TCP socket:

.. code-block:: yaml

    devices:
    - class: Mythen2
      module: mythen2
      transports:
      - type: udp
        url: :1030
      - type: tcp
        url: :1031
      external_signal: :10031

The external signal socket listens for "trigger\n", "low\n" and "high\n"
messages.

Example on how to acquire 10 frames with 0.1s exposure time with trigger start::

    $ nc 0 1031
    -frames 10
    -time 0.1
    -trigen 1
    -start

At this point the simulator acquisition is armed and ready to receive a trigger
to start acquisition. The trigger can be sent with::

    $ nc 0 10031
    trigger
"""

import time
import struct
import logging
import collections

import gevent.event
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
        nb_mod, nb_ch, v = ctx["nmodules"], ctx["modchannels"], ctx[name]
        n = nb_mod * nb_ch
        return struct.pack("<{}{}".format(n, type), *v[:n])

    def decode(ctx, value):
        v = [decoder(i) for i in value]

    return Type(name, encode, decode, default)


def IntArrayNChan(name, default=None):
    return _TypeArrayNChan(name, int, "i", default=default)


TYPES = (
    Str("assemblydate", "2020-07-28" + 40 * "\x00"),
    IntArrayNChan("badchannels", 4 * 1280 * [0]),
    Int("commandid", 0),
    Int("commandsetid", 0),
    Float("dcstemperature", 307.896),
    Float("frameratemax", 987.5),
    Str("fwversion", "01.03.06\x00"),
    FloatArrayNMod("humidity", [13.4, 12.1, 17.9, 11.8]),
    IntArrayNMod("hv", [124, 122, 178, 124]),
    Int("modchannels", 1280),
    Str("modfwversion", 4 * "01.03.07" + "\x00"),
    IntArrayNMod("modnum", [48867, 48868, 48869, 48870]),
    Int("module", 65535),
    Int("nmaxmodules", 4),
    Int("nmodules", 4),
    IntArrayNMod("sensormaterial", [0, 0, 0, 0]),
    IntArrayNMod("sensorthickness", [23, 65, 128, 40]),
    IntArrayNMod("sensorwidth", [678, 432, 4342, 3232]),
    Int("systemnum", 893278),
    FloatArrayNMod("temperature", [308.32, 310.323, 305.4927, 302.4483]),
    Str("testversion", "simula\x00"),
    Str("version", "M4.0.1\x00"),
    Long("time", 10_000_000),
    Int("nbits", 24),
    Int("frames", 1),
    Int("conttrigen", 0),
    Int("gateen", 0),
    Int("gates", 1),
    Int("delbef", 0),
    Int("delafter", 0),
    Int("trigen", 0),
    Int("ratecorrection", 0),
    Int("flatfieldcorrection", 0),
    Int("badchannelinterpolation", 0),
    Int("inpol", 0),  # 0 - rising edge, 1 - falling edge (removed in v4.0)
    Int("outpol", 0),  # 0 - rising edge, 1 - falling edge (removed in v4.0)
    IntArrayNMod(
        "settings", 4 * [0]
    ),  # 0: Standard, 1: Highgain, 2: Fast, 3: Unknown (deprecated since v3.0)
    Str("settingsmode", "auto 5600 11200"),   # (deprecated since v3.0)
    FloatArrayNMod("tau", [4.6, 8.7, 7.4, 2.1]),
    FloatArrayNMod("kthresh", 4 * [6.4]),
    FloatArrayNMod("kthreshmin", 4 * [0.05]),
    FloatArrayNMod("kthreshmax", 4 * [69.56]),
    FloatArrayNMod("energy", 4 * [8.05]),
    FloatArrayNMod("energymin", 4 * [0.05]),
    FloatArrayNMod("energymax", 4 * [69.56]),
)

TYPE_MAP = {t.name: t for t in TYPES}


OK = 4 * b"\x00"


class Protocol(MessageProtocol):
    def read_messages(self):
        transport = self.transport
        while True:
            data = transport.read(self.channel, size=4096)
            if not data:
                return
            yield data


class BaseAcquisition:
    def __init__(
        self, nb_frames, exposure_time, nb_channels, delay_before, delay_after
    ):
        self.nb_frames = nb_frames
        self.exposure_time = exposure_time
        self.nb_channels = nb_channels
        self.delay_before = delay_before
        self.delay_after = delay_after
        self.finished = None
        self.exposing = False
        self.buffer = gevent.queue.Queue()
        self._log = logging.getLogger("simulator.Mythen2")
        self._trigger = gevent.event.Event()

    def trigger(self):
        if self._trigger is None:
            self._log.warn("missed trigger")
        else:
            self._log.debug("trigger!")
            self._trigger.set()

    def run(self):
        self.finished = False
        try:
            for i, data in enumerate(self.steps()):
                self.buffer.put(data)
        finally:
            self.finished = True

    def steps(self):
        raise NotImplementedError

    def expose(self):
        self.exposing = True
        gevent.sleep(self.exposure_time)
        self.exposing = False

    def acquire(self, frame_nb):
        self._log.debug("start acquiring frame #%d/%d...", frame_nb, self.nb_frames)
        if self.delay_before > 0:
            gevent.sleep(self.delay_before)
        self.expose()
        if self.delay_after > 0:
            gevent.sleep(self.delay_after)
        data = self.nb_channels * struct.pack("<i", frame_nb)
        self._log.debug("finished acquiring frame #%d/%d...", frame_nb, self.nb_frames)
        return data


class InternalTriggerAcquisition(BaseAcquisition):
    def steps(self):
        for frame_nb in range(self.nb_frames):
            yield self.acquire(frame_nb)


class TriggerStartAcquisition(BaseAcquisition):
    def steps(self):
        self._trigger.wait()
        for frame_nb in range(self.nb_frames):
            yield self.acquire(frame_nb)


class TriggerMultiAcquisition(BaseAcquisition):
    def steps(self):
        for frame_nb in range(self.nb_frames):
            self._trigger.wait()
            # ignore triggers while acquiring
            self._trigger = None
            yield self.acquire(frame_nb)
            self._trigger = gevent.event.Event()


def start_acquisition(config, nb_frames=None):
    nb_frames = config["frames"] if nb_frames is None else nb_frames
    exp_time = config["time"] * 1e-7
    delay_before, delay_after = config["delbef"] * 1e-7, config["delafter"] * 1e-7
    nb_channels = config["nmodules"] * config["modchannels"]
    trigger_enabled, continuous_trigger = config["trigen"], config["conttrigen"]
    if continuous_trigger:
        klass = TriggerMultiAcquisition
    elif trigger_enabled:
        klass = TriggerStartAcquisition
    else:
        klass = InternalTriggerAcquisition
    acq = klass(nb_frames, exp_time, nb_channels, delay_before, delay_after)
    acq_task = gevent.spawn(acq.run)
    acq_task.acquisition = acq
    return acq_task


class Mythen2(BaseDevice):

    protocol = Protocol

    def __init__(self, *args, **kwargs):
        self.external_signal_address = kwargs.pop("external_signal", None)
        super().__init__(*args, **kwargs)
        self.config = {name: t.default for name, t in TYPE_MAP.items()}
        self.config.update(self.props)
        if self.external_signal_address:
            self.external_signal_source = gevent.server.StreamServer(
                self.external_signal_address, self.on_external_signal
            )
            self.external_signal_source.start()
            self._log.info(
                "listenning for external signal plugs on %r",
                self.external_signal_address,
            )

        self._signal_handler = {
            "trigger": lambda: self.acq.trigger(),
            "high": lambda: self.acq.gate_up(),
            "low": lambda: self.acq.gate_down(),
        }
        self.start_acquisition(0)  # start dummy acquisition

    def __getitem__(self, name):
        return TYPE_MAP[name].encode(self.config)

    def __setitem__(self, name, value):
        TYPE_MAP[name].decode(self.config, value)

    def start_acquisition(self, nb_frames=None):
        self.acq_task = start_acquisition(self.config, nb_frames=nb_frames)
        return self.acq_task

    def on_external_signal(self, sock, addr):
        self._log.info("external signal source plugged: %r", addr)
        fobj = sock.makefile("rwb")
        while True:
            line = fobj.readline()
            if not line:
                self._log.info("external signal unplugged %r", addr)
                return
            signal = line.strip().lower().decode()
            handler = self._signal_handler.get(signal)
            if handler is None:
                self._log.warn("unknown signal %r from %r", signal, addr)
                continue
            try:
                handler()
            except Exception as error:
                self._log.error("error handling external signal %r: %r", signal, error)

    @property
    def acq(self):
        return self.acq_task.acquisition

    def status(self):
        running = 0 if self.acq_task.ready() else 1
        exposing = (1 << 3 if self.acq.exposing else 0) if running else 0
        readout = 1 << 16 if self.acq.buffer.empty() else 0
        return struct.pack("<i", running | exposing | readout)

    def handle_message(self, message):
        self._log.info("handling: %r", message)
        for reply in self._handle_message(message):
            if reply is None:
                self._log.info("return: None")
            else:
                size = len(reply)
                if size > 40:
                    self._log.debug("return (%d) (too big, not shown)", len(reply))
                else:
                    self._log.info("return (%d): %r", len(reply), reply)
            yield reply

    def _handle_message(self, message):
        message = message.strip().decode()
        assert message[0] == "-"
        cmd, *data = message.split(" ", 1)
        cmd = cmd[1:]
        self.config["commandid"] += 1
        if cmd != "get":
            self.config["commandsetid"] += 1
        if cmd == "get":
            assert len(data) == 1
            data = data[0]
            if data == "status":
                yield self.status()
            else:
                yield self[data]
        elif cmd == "reset":
            gevent.sleep(2 + 0.5 * self.config["nmodules"])
            yield OK
        elif cmd == "start":
            self.start_acquisition()
            yield OK
        elif cmd == "stop":
            self.acq_task.kill()
            yield OK
        elif cmd == "readout":
            nb_frames = int(data[0]) if data else 1
            for _ in range(nb_frames):
                yield self.acq.buffer.get()
        else:
            assert len(data) == 1
            self[cmd] = data[0]
            yield OK
