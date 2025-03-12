# -*- coding: utf-8 -*-
#
# This file is part of the sinstruments project
#
# Copyright (c) 2018-present Tiago Coutinho
# Distributed under the GPLv3 license. See LICENSE for more info.

"""
Elettra electometer simulator helper classes

To create an elettra electrometer device use the following configuration as
a starting point:

.. code-block:: yaml

    devices:
        - class: ElettraElectrometer
          module: elettra
          transports:
              - type: tcp
                url: :10001

A simple *nc* client can be used to connect to the instrument:

    $ nc 0 10001

"""

import random
import functools

import gevent

from sinstruments.simulator import BaseDevice


def Cmd(default=None, allowed=None, access="rw", dtype=None):
    return dict(default=default, access=access, dtype=dtype, allowed=allowed)


CmdR = functools.partial(Cmd, access="r")
CmdW = functools.partial(Cmd, access="w")
CmdRW = Cmd


def __build_types(g=None):
    g = g or globals()
    # define 'ICmdR', 'ICmdRW', ...
    for dtype in (("B", bool), ("I", int), ("S", str)):
        typ_str, typ = dtype
        for access in ("r", "w", "rw"):
            name = "{0}Cmd{1}".format(typ_str, access.upper())
            g[name] = functools.partial(Cmd, access=access, dtype=typ)


__build_types()


class ElettraElectrometer(BaseDevice):
    """
    Base class for Elettra electrometer
    """

    ACK = "ACK"
    NAK = "NAK"
    TERM = "\r\n"

    COMMANDS = dict(
        get=CmdR(),
        acq=BCmdRW(False, [True]),
        bdr=ICmdRW(921600, [921600, 460800, 230400, 115200, 57600, 38400, 19200, 9600]),
        bin=BCmdRW(True),
        naq=ICmdRW(0, dict(min=0, max=2000000000)),
        rng=ICmdRW(0, [0, 1, 2]),
        trg=BCmdRW(False),
        ver=SCmdR("AH501D 2.0.3"),
    )

    ASCII_INT = "{0:06x}"
    SHORT_GET = "g"

    def __init__(self, name, **opts):
        super_kwargs = {}
        super_kwargs["newline"] = opts.pop("newline", "\r")
        super(ElettraElectrometer, self).__init__(name, **super_kwargs)
        for k, v in self.COMMANDS.items():
            opts.setdefault(k, v["default"])
        self.commands = opts
        firmware_version_str = self["ver"].rsplit(" ", 1)[1]
        firmware_version = tuple(map(int, firmware_version_str.split(".")))
        self.firmware_version = firmware_version
        self.acq_task = None

    def encode(self, cmd, value):
        cmd = cmd.lower()
        cmd_info = self.COMMANDS[cmd]
        dtype = cmd_info["dtype"]
        if dtype == bool:
            return "ON" if value else "OFF"
        # TODO: handle binary mode
        return str(value)

    def decode(self, cmd, value):
        cmd = cmd.lower()
        cmd_info = self.COMMANDS[cmd]
        dtype = cmd_info["dtype"]
        allowed = cmd_info["allowed"]
        rvalue = value
        if dtype == bool:
            rvalue = value.upper() == "ON"
            if allowed and rvalue not in allowed:
                raise ValueError("set {0!r} to {1} not allowed".format(cmd, value))
        if dtype == int:
            rvalue = int(value)
            if isinstance(allowed, dict):
                minim = allowed.get("min", float("-inf"))
                maxim = allowed.get("max", float("inf"))
                if rvalue < minim or rvalue > maxim:
                    raise ValueError("set {0!r} to {1} outside allowed range".format(cmd, value))
            elif isinstance(allowed, (tuple, list, dict, set)):
                if rvalue not in allowed:
                    raise ValueError("set {0!r} not in allowed values".format(cmd))
        return rvalue

    def __getitem__(self, cmd):
        cmd = cmd.lower()
        command = self.COMMANDS[cmd]
        if "r" not in command["access"]:
            raise ValueError("{0} is not readable".format(cmd))
        return self.commands[cmd]

    def __setitem__(self, cmd, value):
        cmd = cmd.lower()
        command = self.COMMANDS[cmd]
        if "w" not in command["access"]:
            raise ValueError("{0} is not writable".format(cmd))
        self.commands[cmd] = value

    def handle_message(self, line):
        self._log.debug("processing line %r", line)
        line = line.strip()
        if line.lower() == self.SHORT_GET:
            result = self.get
        elif line.endswith("?"):
            result = self.handle_read(line.rsplit(" ", 1)[0])
        else:
            result = self.handle_write(line)
        if result is not None:
            result += self.TERM
            self._log.debug("answering with %r", result)
            return result

    def handle_read(self, line):
        try:
            return self._handle_read(line)
        except Exception as err:
            self._log.error("error running '%s ?': %s", line, err)
            self._log.debug("details: %s", err, exc_info=1)
            return self.NAK

    def _handle_read(self, line):
        args = line.split()
        cmd, args = args[0].lower(), args[1:]
        if hasattr(self, cmd):
            return getattr(self, cmd)()
        if cmd in self.commands:
            value = self[cmd]
            return "{0} {1}".format(cmd.upper(), self.encode(cmd, value))
        else:
            return self.NAK

    def handle_write(self, line):
        try:
            return self._handle_write(line)
        except Exception as err:
            self._log.error("error running '%s': %s", line, err)
            self._log.debug("details: %s", err, exc_info=1)
            return self.NAK

    def _handle_write(self, line):
        args = line.split()
        cmd, args = args[0].lower(), args[1:]
        arg = args[0] if args else None
        if hasattr(self, cmd):
            return getattr(self, cmd)(arg)
        if cmd in self.commands:
            self[cmd] = self.decode(cmd, arg)
            return self.ACK
        return self.NAK

    def acq(self, value=None):
        if value is None:
            return "ACQ " + "ON" if self.acq_task else "OFF"
        on = value.upper() == "ON"
        if not on or self.acq_task:
            return self.NAK
        self.acq_task = gevent.spawn(self.do_acq)
        return self.ACK

    def _generate(self):
        nb_channels = self.commands.get("chn", 4)
        res = self.commands.get("res", 24)
        return [random.randrange(0, 2**res) for _ in range(nb_channels)]

    def get(self):
        values = self._generate()
        # TODO: handle binary
        values = map(self.ASCII_INT.format, values)
        return " ".join(values)

    def do_acq(self, nap=0.01):
        while True:
            self.broadcast(self.get() + self.TERM)
            gevent.sleep(nap)


class AH401D(ElettraElectrometer):
    COMMANDS = dict(
        ElettraElectrometer.COMMANDS,
        hlf=BCmdRW(False),
        itm=ICmdRW(1000, dict(min=10, max=10000)),
        rng=SCmdRW("1", map(str, [0, 1, 2, 3, 4, 5, 6, 7, "XY"])),
        sum=BCmdRW(False),
        ver=SCmdR("AH401D 2.0.3"),
    )
    COMMANDS["?"] = CmdR()

    ASCII_INT = "{0}"
    SHORT_GET = "?"


class AH501D(ElettraElectrometer):
    # special messages are sent without '\r'
    # TODO: NOT HANDLED BY SIMULATOR ANYMORE: NEED TO IMPLEMENT A SPECIFIC PROTOCOL
    special_messages = set(["S"])  # stop continuous acquisition

    COMMANDS = dict(
        ElettraElectrometer.COMMANDS,
        g=CmdR(),
        chn=ICmdRW(4, [1, 2, 4]),
        dec=BCmdRW(False),
        hvs=BCmdRW(False),
        res=ICmdRW(24, [16, 24]),
        syn=CmdW(),
        ver=SCmdR("AH501D 2.0.3"),
    )

    ASCII_INT = "{0:06x}"
    SHORT_GET = "g"

    def handle_write(self, line):
        cmd = line.strip().split(" ", 1)[0].upper()
        if cmd == "S":
            if self.acq_task:
                self.acq_task.kill()
                return self.ACK
            else:
                return self.NAK
        result = super(AH501D, self).handle_write(line)
        # No result is sent after an 'ACQ ON' command
        if cmd != "ACQ":
            return result
