# -*- coding: utf-8 -*-
#
# This file is part of the instrument simulator project
#
# Copyright (c) 2018 Tiago Coutinho
# Distributed under the MIT. See LICENSE for more info.

"""IcePAP_ motor controller simulator device"""

__all__ = ["IcePAP", "Axis", "IcePAPError"]

import re
import enum
import inspect
import logging
import weakref
import functools
import time
import threading

from sinstruments.simulator import BaseDevice


MAX_AXIS = 128


def iter_axis(start=1, stop=MAX_AXIS + 1, step=1):
    start, stop = max(start, 1), min(stop, MAX_AXIS + 1)
    for i in range(start, stop, step):
        if i % 10 > 8:
            continue
        yield i


VALID_AXES = list(iter_axis())


def default_parse_args(icepap, query=True, broadcast=False, args=(), two_string=False):
    logging.debug("Using default_parse_args")
    args = list(args)
    args = args[1:] if two_string else args
    if query:
        if broadcast:
            axes = sorted(icepap._axes.keys())
        else:
            axes, args = args, ()
    else:
        if broadcast:
            axes = sorted(icepap._axes.keys())
            args = len(axes) * [args[0]]
        else:
            # logging.debug("args list is %r", args)
            axes, args = args[::2], args[1::2]
    return axes, args


two_string_command_parse_args = functools.partial(default_parse_args, two_string=True)

axis_value_parse_args = default_parse_args


def value_axes_parse_args(icepap, query=True, broadcast=False, args=()):
    logging.debug("Using value_axes_parse_args")
    if query:
        if broadcast:
            axes = sorted(icepap._axes.keys())
        else:
            axes, args = args, ()
    else:
        if broadcast:
            axes = sorted(icepap._axes.keys())
        else:
            args = list(args)
            axes = args[1:]
        args = len(axes) * [args[0]]
    logging.debug("Axes %r", axes)
    logging.debug("Args %r", args)
    return axes, args


def args_axes_parse_args(icepap, query=True, broadcast=False, args=()):
    logging.debug("Using args_axes_parse_args")
    if not query and not broadcast:
        args = list(args)
        axes, args = args[1:], args[:1]
    else:
        axes, args = default_parse_args(icepap, query, broadcast, args)
    logging.debug("Axes %r", axes)
    logging.debug("Args %r", args)
    return axes, args


def axis_parse_args(icepap, query=True, broadcast=False, args=()):
    axes = list(args)
    args = ()
    logging.debug("Axes %r", axes)
    logging.debug("Args %r", args)
    return axes, args


class IcePAPError(enum.Enum):
    CommandNotRecognized = "Command not recognised"
    CannotBroadCastQuery = "Cannot broadcast a query"
    CannotAcknowledgeBroadcast = "Cannot acknowledge a broadcast"
    WrongParameters = "Wrong parameter(s)"
    WrongNumberParameters = "Wrong number of parameter(s)"
    TooManyParameters = "Too many parameters"
    InvalidControllerBoardCommand = "Command or option not valid in controller boards"
    BadBoardAddress = "Bad board address"
    BoardNotPresent = "Board is not present in the system"


def axis_command(func_or_name=None, mode="rw", default=None, cfg_info=None):
    if func_or_name is None:
        # used as a decorator with no arguments
        return functools.partial(axis_command, default=default, mode=mode)

    if callable(func_or_name):
        logging.debug("Callable axis command %r created", func_or_name.__name__)
        # used directly as a decorator
        func, name = func_or_name, func_or_name.__name__
        if default is not None:
            ValueError(
                "Cannot give 'default' in method '{0}' " "decorator".format(name)
            )
    else:
        name = func_or_name
        attr_name = "_" + name
        logging.debug("Attribute axis command %r created", attr_name)
        if default is None:
            raise ValueError("Must give default string value")

        def func(self, ack=False, value=None):
            if value is None:
                return getattr(self, attr_name, default)
            setattr(self, attr_name, value)
            return "OK"

    func._name = name
    func._mode = mode
    func._cfg_info = cfg_info
    return func


axis_read_command = functools.partial(axis_command, mode="r")


class DriverPresence(enum.Enum):
    Offset = 0
    NotPresent = 0
    NotResponsive = 1
    ConfigurationMode = 2
    Alive = 3


class DriverMode(enum.Enum):
    Offset = 2
    Oper = 0 << 2
    Prog = 1 << 2
    Test = 2 << 2
    Fail = 3 << 2


class DriverDisable(enum.Enum):
    Offset = 4
    PowerEnabled = 0 << 4
    NotActive = 1 << 4
    Alarm = 2 << 4
    RemoteRackDisableInputSignal = 3 << 4
    LocalRackDisableSwitch = 4 << 4
    RemoteAxisDisableInputSignal = 5 << 4
    LocalAxisDisableSwitch = 6 << 4
    SoftwareDisable = 7 << 4


class DriverIndexer(enum.Enum):
    Offset = 7
    Internal = 0 << 7
    InSystem = 1 << 7
    External = 2 << 7
    Linked = 3 << 7


class DriverReady(enum.Enum):
    Offset = 9
    # NotReady = 0 << 9
    # Ready = 1 << 9
    NotReady = 0
    Ready = 1


class DriverMoving(enum.Enum):
    Offset = 10
    # NotMoving = 0 << 10
    # Moving = 1 << 10
    NotMoving = 0
    Moving = 1


class DriverSettling(enum.Enum):
    Offset = 11
    # NotSettling = 0 << 11
    # Settling = 1 << 11
    NotSettling = 0
    Settling = 1


class DriverOutOfWindow(enum.Enum):
    Offset = 12
    # NotOutOfWindow = 0 << 12
    # OutOfWindow = 1 << 12
    NotOutOfWindow = 0
    OutOfWindow = 1


class DriverWarning(enum.Enum):
    Offset = 13
    # NotWarning = 0 << 13
    # Warning = 1 << 13
    NotWarning = 0
    Warning = 1


class DriverStopCode(enum.Enum):
    subregister = [14, 15, 16, 17]
    Offset = 14
    # EndOfMotion = 0 << 14
    # Stop = 1 << 14
    # Abort = 2 << 14
    # LimitPos = 3 << 14
    # LimitNeg = 4 << 14
    # ConfiguredStop = 5 << 14
    # Disabled = 6 << 14
    # NotStop = 7 << 14
    # InternalFailure = 8 << 14
    # MotorFailure = 9 << 14
    # PowerOverload = 10 << 14
    # DriverOverheading = 11 << 14
    # CloseLoopError = 12 << 14
    # ControlEncoderError = 13 << 14
    # ExternalAlarm = 15 << 14
    EndOfMotion = 0
    Stop = 1
    Abort = 2
    LimitPos = 3
    LimitNeg = 4
    ConfiguredStop = 5
    Disabled = 6
    NotStop = 7
    InternalFailure = 8
    MotorFailure = 9
    PowerOverload = 10
    DriverOverheading = 11
    CloseLoopError = 12
    ControlEncoderError = 13
    ExternalAlarm = 15


class LimitPos(enum.Enum):
    Offset = 18
    # NotActive = 0 << 18
    # Active = 1 << 18
    NotActive = 0
    Active = 1


class LimitNeg(enum.Enum):
    Offset = 19
    # NotActive = 0 << 19
    # Active = 1 << 19
    NotActive = 0
    Active = 1


class PowerOn(enum.Enum):
    Offset = 23
    # On = 1 << 23
    # Off = 0 << 23
    On = 1
    Off = 0


# setBit() returns an integer with the bit at 'offset' set to 1.
def setMask(int_type, value, offset, subregister=None):
    if subregister is not None:
        for bit in subregister:
            mask = ~(1 << bit)
            int_type = (int_type & mask)
    if value == 0:
        mask = ~(1 << offset)
        return (int_type & mask)
    mask = value << offset
    return (int_type | mask)


# setBit() returns an integer with the bit at 'offset' set to 1.
def testMask(int_type, value, offset):
    mask = value << offset
    return (int_type & mask)


# testBit() returns a nonzero result, 2**offset, if the bit at 'offset' is one.
def testBit(int_type, offset):
    mask = 1 << offset
    return (int_type & mask)


# setBit() returns an integer with the bit at 'offset' set to 1.
def setBit(int_type, offset):
    mask = 1 << offset
    return (int_type | mask)


# clearBit() returns an integer with the bit at 'offset' cleared.
def clearBit(int_type, offset):
    mask = ~(1 << offset)
    return (int_type & mask)


# toggleBit() returns an integer with the bit at 'offset' inverted, 0 -> 1 and 1 -> 0.
def toggleBit(int_type, offset):
    mask = 1 << offset
    return (int_type ^ mask)


class Axis(object):
    """IcePAP emulated axis"""

    def __init__(self, icepap, address=None, **opts):
        self.__icepap = weakref.ref(icepap)
        self.__motion = None
        self.__status = 0x00A00203
        if opts["addr"]:
            address = opts["addr"]
        self.address = address
        if address not in VALID_AXES:
            raise ValueError("{0} is not a valid address".format(address))
        self._log = logging.getLogger("{0}.{1}".format(icepap._log.name, address))
        for k, v in opts.items():
            self._log.debug("YML atribute %r added", k)
            setattr(self, "_" + k, v)
        self._name = opts.get("axis_name", "")

        self._start_time = None
        self._destination = None
        self._t1 = 0.0
        self._t2 = 0.0
        self._t3 = 0.0
        self._x0 = 0.0
        self._x1 = 0.0
        self._x2 = 0.0
        self._sign = 1

        self._is_stopping = False
        self.stop_position = None
        self.current_velocity = 0
        self._on_position = False
        self.in_movement(False)
        self.setStatus(DriverStopCode.NotStop.value, DriverStopCode.Offset.value)

        if opts["power"] == "ON" or "True":
            self.setStatus(PowerOn.On.value, PowerOn.Offset.value)
        else:
            self.setStatus(PowerOn.Off.value, PowerOn.Offset.value)

        if getattr(self, "high_lim", None)() != "NO":
            self.setStatus(LimitPos.Active.value, LimitPos.Offset.value)
        if getattr(self, "low_lim", None)() != "NO":
            self.setStatus(LimitNeg.Active.value, LimitNeg.Offset.value)

    @property
    def _icepap(self):
        return self.__icepap()

    def reset(self):
        self._start_time = None
        self._destination = None
        self._t1 = 0.0
        self._t2 = 0.0
        self._t3 = 0.0
        self._x0 = 0.0
        self._x1 = 0.0
        self._x2 = 0.0
        self._sign = 1

        self._is_stopping = False
        self.stop_position = None
        self.current_velocity = 0
        self.setStatus(DriverStopCode.NotStop.value, DriverStopCode.Offset.value, DriverStopCode.subregister.value)
        self.in_movement(False)


    def in_movement(self, moving):
        if moving:
            self.setStatus(DriverReady.NotReady.value, DriverReady.Offset.value)
            self.setStatus(DriverMoving.Moving.value, DriverMoving.Offset.value)
        else:
            self.setStatus(DriverReady.Ready.value, DriverReady.Offset.value)
            self.setStatus(DriverMoving.NotMoving.value, DriverMoving.Offset.value)

    def is_ready(self):
        return self.checkStatus(DriverReady.Ready.value, DriverReady.Offset.value)

    def setStatus(self, value, offset, subregister=None):
        status = int(getattr(self, "statusregister", None)(), 16)
        status = setMask(status, value, offset, subregister)
        setattr(self, "_statusregister", hex(status))

    def checkStatus(self, value, offset):
        status = int(getattr(self, "statusregister", None)(), 16)
        return testMask(status, value, offset)

            # --- Axis level commands

    def preparemove(self, args=(), cmd_result=None, **kwargs):
        self._destination = float(args)
        position = float(getattr(self, "pos", None)())
        if getattr(self, "power", None)() == "OFF":
            raise RuntimeError("Power is OFF")
        if self.checkStatus(LimitPos.Active.value, LimitPos.Offset.value):
            max_position = float(getattr(self, "high_lim", None)())
            if self._destination > max_position:
                self.setStatus(DriverStopCode.LimitPos.value, DriverStopCode.Offset.value, DriverStopCode.subregister.value)
                raise ValueError("ERROR: position not reachable")
        if self.checkStatus(LimitNeg.Active.value, LimitNeg.Offset.value):
            min_position = float(getattr(self, "low_lim", None)())
            if self._destination < min_position:
                self.setStatus(DriverStopCode.LimitNeg.value, DriverStopCode.Offset.value, DriverStopCode.subregister.value)
                raise ValueError("ERROR: position not reachable")
        if position == self._destination:
            self._log.debug("ALREADY ON POSITION")
            self._on_position = True
            # self.reset()
            return " OK"
        else:
            self._on_position = False
            velocity = float(getattr(self, "velocity", None)())
            acctime = float(getattr(self, "acctime", None)())
            acceleration = velocity / acctime
            if not self.is_ready():
                # self.reset()
                raise RuntimeError("Can't change movemement while moving")

            self._sign = 1
            if position > self._destination:
                self._sign = -1

            self._t1 = velocity / acceleration

            self._x0 = position
            self._x1 = self._x0 + self._sign * acceleration * (self._t1 ** 2) / 2
            if self._t1 == self._t2:
                self._x2 = self._x1
            else:
                self._x2 = self._x1 + self._sign * (abs(self._x0 - self._destination) - 2 *
                                                    abs(self._x0 - self._x1))

            self._t2 = self._t1 + abs(self._x2 - self._x1) / velocity
            self._t3 = self._t1 + self._t2
            if 2 * self._t1 > self._t3:
                raise ValueError("Acceleration time too slow")

            self._log.debug("movement prepared for %r", self.address)
            return " OK"

    def startmove(self, args=(), cmd_result=None, **kwargs):
        if getattr(self, "power", None)() == "OFF":
            raise RuntimeError("Power is OFF")
        if not self._on_position:
            self.setStatus(DriverStopCode.NotStop.value, DriverStopCode.Offset.value, DriverStopCode.subregister.value)
            self.in_movement(True)
            self._start_time = time.time()
            self._log.debug("move %r to %r", self.address, self._destination)
        return " OK"

    def position(self,  ack=False, *args):
        if ack:
            args = list(args)
            args = args[1]
            if not self.is_ready():
                # self.reset()
                raise RuntimeError("Can't change movemement while moving")
            pos = float(args)
            setattr(self, "_position", pos)
            self.reset()
            return " OK"
        else:
            velocity = float(getattr(self, "velocity", None)())
            acctime = float(getattr(self, "acctime", None)())
            position = float(getattr(self, "position", None)())
            acceleration = velocity / acctime

            # self._log.debug("self._start_time = %r ", self._start_time)
            # self._log.debug("self._destination = %r ", self._destination)
            # self._log.debug("self._t1 = %r ", self._t1)
            # self._log.debug("self._t2 = %r ", self._t2)
            # self._log.debug("self._t3 = %r ", self._t3)
            # self._log.debug("self._x0 = %r ", self._x0)
            # self._log.debug("self._x1 = %r ", self._x1)
            # self._log.debug("self._x2 = %r ", self._x2)
            # self._log.debug("self._sign = %r ", self._sign)

            if self._start_time == None or self._destination == None:
                return int(position)
            if position == self._destination:
                self._log.debug("MOVEMENT STOPED")
                setattr(self, "_position", position)
                self._start_time = None
                self.in_movement(False)
                self.setStatus(DriverStopCode.EndOfMotion.value, DriverStopCode.Offset.value, DriverStopCode.subregister.value)
                # self.reset()
                return int(position)
            if self._is_stopping:
                current_time = time.time() - self._start_time
                if acctime <= current_time:
                    self._is_stopping = False
                    position = self.stop_position + self._sign * (self.current_velocity * acctime) - self._sign * \
                               (acceleration * (acctime ** 2)) / 2
                    setattr(self, "_position", position)
                    self._start_time = None
                    self.in_movement(False)
                    # self.reset()
                    return int(position)
                else:
                    position = self.stop_position + self._sign * (self.current_velocity * current_time) - self._sign * \
                                     (acceleration * (current_time ** 2)) / 2
                    setattr(self, "_position", position)
                    return int(position)

            else:
                current_time = time.time() - self._start_time
                if current_time <= self._t1:
                    #self._log.debug("FASE 1")
                    position = self._x0 + self._sign * (acceleration * (current_time ** 2)) / 2
                    self.current_velocity = 0 + acceleration * current_time
                    setattr(self, "_position", position)

                    return int(position)

                elif self._t1 < current_time < self._t2:
                    #self._log.debug("FASE 2")
                    current_time -= self._t1
                    position = self._x1 + self._sign * velocity * current_time
                    self.current_velocity = velocity
                    setattr(self, "_position", position)
                    return int(position)

                elif self._t2 < current_time < self._t3:
                    #self._log.debug("FASE 3")
                    current_time -= self._t2
                    position = self._x2 + self._sign * velocity * current_time - self._sign * \
                                     (acceleration * (current_time ** 2)) / 2
                    self.current_velocity = velocity - acceleration * current_time
                    setattr(self, "_position", position)
                    return int(position)

                position = self._destination
                self._log.debug("MOVEMENT STOPED")
                setattr(self, "_position", position)
                self._start_time = None
                self.in_movement(False)
                self.setStatus(DriverStopCode.EndOfMotion.value, DriverStopCode.Offset.value, DriverStopCode.subregister.value)

                return int(position)

    def abort(self):
        if not self.is_ready():
            self._log.debug("Axe %r is not moving", self.address)
            return " OK"
        position = float(getattr(self, "pos", None)())
        self._start_time = None
        self.setStatus(DriverStopCode.Abort.value, DriverStopCode.Offset.value, DriverStopCode.subregister.value)
        self._log.debug("axis r% aborted at position %r ", self.address, position)
        return " OK"

    def stop(self):
        if self.is_ready():
            self._log.debug("Axe %r is not moving", self.address)
            return " OK"
        self.stop_position = float(getattr(self, "pos", None)())
        self.setStatus(DriverStopCode.Stop.value, DriverStopCode.Offset.value, DriverStopCode.subregister.value)
        self._start_time = time.time()
        self._is_stopping = True
        self._log.debug("axis r% aborted at position %r ", self.address, self.stop_position)
        return " OK"

    def power(self, ack=None, args=()):
        update = float(getattr(self, "pos", None)())
        self._log.debug("Updated position value is %r", update)
        if args:
            if args == "ON" or args == "on":
                self.setStatus(PowerOn.On.value, PowerOn.Offset.value)
            else:
                self.setStatus(PowerOn.Off.value, PowerOn.Offset.value)
            return " OK"
        else:
            if self.checkStatus(PowerOn.On.value, PowerOn.Offset.value):
                return "ON"
            else:
                return "OFF"

    def status(self, ack=None, args=()):
        update = float(getattr(self, "pos", None)())
        self._log.debug("Updated position value is %r", update)
        return hex(int(getattr(self, "statusregister", None)(), 16))

    def stopcode(self, ack=None, args=()):
        update = float(getattr(self, "pos", None)())
        self._log.debug("Updated position value is %r", update)
        stopcode = 0x0
        for bit, offset in zip(DriverStopCode.subregister.value, range(len(DriverStopCode.subregister.value))):
            self._log.debug("Subregister value is %r", bit)
            self._log.debug("Subregister value is %r", offset)
            if self.checkStatus(1, bit):
                stopcode = setBit(stopcode, offset)
        return stopcode

    # --- Axis level instrucctions

    active = axis_read_command("active", default="YES", cfg_info=bool)
    mode = axis_read_command("mode", default="OPER")
    statusregister = axis_command("statusregister", default="0x00A00203")
    status = fstatus = axis_command(status)
    vstatus = axis_read_command("vstatus", default="TODO")
    # stopcode = axis_read_command("stopcode", default="0x0002")
    stopcode = axis_read_command(stopcode)
    vstopcode = axis_read_command("vstopcode", default="TODO")
    alarm = axis_read_command("alarm", default="NO")
    warning = axis_read_command("warning", default="NO")
    wtemp = axis_command("wtemp", default=45)
    config = axis_command("config", default="blissadm@lid00b_2015/10/09_16:08:00")
    cfg = axis_command("cfg", default="TODO")
    cfginfo = axis_read_command("cfginfo", default="TODO")
    cswitch = axis_command("cswitch", default="NORMAL")
    ver = axis_read_command("ver", default=" 3.15")
    name = axis_command("name", default="")
    id = axis_read_command("id", default="0008.0165.D333")
    post = axis_read_command("post", default=0)
    # power = axis_command("power", default="OFF")
    power = axis_command(power)
    auxps = axis_command("auxps", default="ON")
    pos = fposition = posaxis = fpos = axis_command(position)
    position = axis_command("position", default=0)
    high_lim = axis_command("high_lim", default="NO")
    low_lim = axis_command("low_lim", default="NO")
    enc = axis_command("enc", default=0)

    velocity = axis_command("velocity", default=1000)
    acctime = axis_command("acctime", default=0.25)

    abort = axis_command(abort)
    preparemove = axis_command(preparemove)
    startmove = axis_command(startmove)
    stop = axis_command(stop)

    @axis_read_command
    def help(self):
        return _build_help(self)




def __create_command(name, mode, arg_parser):
    pass


def _argspec_kwargs(argspec):
    return dict(zip(*[reversed(l) for l in (argspec.args, argspec.defaults or [])]))


def _func_kwargs(f):
    return _argspec(inspect.getargspec(f))


def _call_compatible_kwargs(f, *args, **kwargs):
    argspec = inspect.getargspec(f)
    if len(args) > len(argspec.args):
        return IcePAPError.TooManyParameters
    if argspec.keywords is None:
        f_kwargs = _argspec_kwargs(argspec)
        kwargs = dict([(k, kwargs[k]) for k in set(kwargs) & set(f_kwargs)])
    return f(*args, **kwargs)


def _result(cmd_result, result):
    if isinstance(result, IcePAPError):
        result = "ERROR {0}".format(result.value)
    return "{0} {1}".format(cmd_result, result)


def _build_help(obj):
    klass = obj.__class__
    items = []
    max_read, max_write = 0, 0
    for name in sorted(dir(obj)):
        if name.startswith("_"):
            continue
        if hasattr(klass, name) and inspect.isdatadescriptor(getattr(klass, name)):
            continue
        member = getattr(obj, name)
        if callable(member):
            mode = getattr(member, "_mode", None)
            if mode is None:
                continue
            name = member._name
        else:
            if hasattr(klass, name):
                continue
            mode = "rw"
        name = name.upper()
        read = "?" + name if "r" in mode else ""
        write = name if "w" in mode else ""
        max_read = max(max_read, len(read))
        max_write = max(max_write, len(write))
        items.append((read, write))
    templ = "{{0:>{0}}}  {{1:<}}".format(max_write + 2)
    return "$\n" + "\n".join([templ.format(w, r) for r, w in items]) + "\n$"


def command(f_or_name=None, mode="rw", axes_arg_parser=None, default=None):
    if f_or_name is None:
        # used as a decorator with no arguments
        return functools.partial(command, mode=mode, axes_arg_parser=axes_arg_parser)
    if callable(f_or_name):
        logging.debug("Callable axes command %r created", f_or_name.__name__)
        # used directly as a decorator
        f, name = f_or_name, f_or_name.__name__
        if default is not None:
            ValueError(
                "Cannot give 'default' in method '{0}' " "decorator".format(name)
            )
    else:
        name = f_or_name
        attr_name = "_" + name
        logging.debug("Attribute axes command %r created", attr_name)

        def f(self, *args, **kwargs):
            is_query = kwargs.get("is_query", True)
            attr = getattr(self, attr_name, None)
            if is_query:
                return default if attr is None else attr
            else:
                setattr(self, attr_name, args[0])
                return "OK"

        f.__name__ = name

    @functools.wraps(f)
    def wraps(self, **kwargs):
        logging.debug("Esto si se ejecuta: %r", kwargs)
        is_query = kwargs["is_query"]
        if (is_query and "r" not in mode) or (not is_query and "w" not in mode):
            return IcePAPError.CommandNotRecognized

        args = kwargs["args"]
        ack = kwargs["ack"]
        broadcast = kwargs["broadcast"]
        cmd_result = kwargs["cmd_result"]

        # axis(es) command
        if axes_arg_parser:
            axes, args = axes_arg_parser(self, is_query, broadcast, args)
            logging.debug("Axes %r", axes)
            logging.debug("Args %r", args)
            result = []
            if is_query or axes_arg_parser == axis_parse_args:
                for axis in axes:
                    axis = self._get_axis(axis, system=True)
                    if not isinstance(axis, Axis):
                        return _result(cmd_result, axis)
                    # TODO: handle errors

                    result.append(str(getattr(axis, name)()))
                if axes_arg_parser == axis_parse_args:
                    result = result[0]
                else:
                    result = " ".join(result)
            else:
                self._log.debug("error %r ", zip(axes, args))
                for axis, arg in zip(axes, args):
                    axis = self._get_axis(axis, system=True)
                    if not isinstance(axis, Axis):
                        return _result(cmd_result, axis)
                    # TODO: handle errors
                    result.append(str(getattr(axis, name)(ack, arg)))
                result = result[0]
        else:
            result = _call_compatible_kwargs(f, self, *args, **kwargs)
        return _result(cmd_result, result)

    wraps._name = name
    wraps._mode = mode
    return wraps


read_command = functools.partial(command, mode="r")

axes_command = functools.partial(command, axes_arg_parser=axis_value_parse_args)
axes_read_command = functools.partial(axes_command, mode="r")


class IcePAP(BaseDevice):
    """Emulated IcePAP"""

    _ACK = "(?P<ack>#)?"
    _ADDR = "((?P<addr>\d+)?(?P<broadcast>\:))?"
    _QUERY = "(?P<is_query>\\?)?"
    # _INSTR = "(?P<instr>\\w+\\s*[a-zA-Z]*)"
    _INSTR = "(?P<instr>\\w+)"
    _CMD = re.compile(
        "{ack}\s*{addr}\s*{query}{instr}\s*".format(
            ack=_ACK, addr=_ADDR, query=_QUERY, instr=_INSTR
        )
    )
    DEFAULT_NEWLINE = b"\r"

    def __init__(self, name, axes=None, **opts):
        # self.DEFAULT_NEWLINE.decode()
        super_kwargs = dict(newline=opts.pop("newline", self.DEFAULT_NEWLINE))
        super(IcePAP, self).__init__(name, **super_kwargs)
        self._log.debug("super kwargs %r", super_kwargs)
        axes_dict = {}
        for axis in axes or [dict(addr=addr) for addr in iter_axis()]:
            self._log.debug("axis %r created", axis)
            axes_dict[axis["addr"]] = Axis(self, **axis)
        self._axes = axes_dict
        for k, v in opts.items():
            setattr(self, "_" + k, v)

    @staticmethod
    def _cmd_result(cmd_match):
        """retrieve the command error message prefix from the command line"""
        groups = cmd_match.groupdict()
        # replace None values with ''
        groups_str = dict([(k, ("" if v is None else v)) for k, v in groups.items()])
        groups_str["instr"] = groups_str["instr"].upper().strip()
        cmd_err = "{addr}{broadcast}{is_query}{instr}".format(**groups_str)
        return cmd_err

    def _get_axis(self, addr, system=False):
        """Recieve axis addres and checks if it's a valid addres for the given conf.yml
        Different error mesage strings are stored on class IcePAPError"""
        if addr is None:
            return IcePAPError.WrongNumberParameters
        try:
            addr = int(addr)
        except ValueError:
            return IcePAPError.WrongParameters
        if addr is 0:
            return IcePAPError.InvalidControllerBoardCommand
        if addr > 256:
            return IcePAPError.WrongParameters
        if addr not in VALID_AXES:
            err = IcePAPError.BadBoardAddress
            if system:
                return "ERROR Axis {0}: {1}".format(addr, err.value)
            return err
        if addr not in self._axes:
            err = IcePAPError.BoardNotPresent
            if system:
                return "ERROR Axis {0}: {1}".format(addr, err.value)
            return err
        return self._axes[addr]

    def handle_line(self, line):
        line = line.decode()
        # self._log.debug("line type %r", type(line))
        self._log.debug("processing line %r", line)
        line = line.strip()
        responses = []
        for cmd in line.split(";"):
            cmd = cmd.strip()
            response = self.handle_command(cmd)
            if response is not None:
                responses.append(response.encode('ascii') + b"\n")
        if responses:
            result = b"".join(responses)
            self._log.debug("answering with %r", result)
            return result

    def handle_command(self, cmd):
        self._log.debug("processing command %r", cmd)
        if not cmd:
            return
        # cmd = cmd.decode()
        cmd_match = self._CMD.match(cmd)
        if cmd_match is None:
            self._log.info("unable to parse command")
            cmd_result = cmd.replace("#", "").strip().split(" ", 1)[0]
            return _result(cmd_result, IcePAPError.CommandNotRecognized)
        groups = cmd_match.groupdict()
        ack, addr = groups["ack"], groups["addr"]
        broadcast, is_query = groups["broadcast"], groups["is_query"]
        groups["instr"] = groups["instr"].strip()
        # instr = groups["instr"].lower().replace(" ", "")
        instr = groups["instr"].lower().replace(" ", "")

        self._log.debug("processing group %r", groups)
        cmd_result = IcePAP._cmd_result(cmd_match)
        cmd_result = cmd_result.replace(" ", "")
        self._log.debug("Command result %r", cmd_result)

        if addr is not None:
            try:
                addr = int(addr)
            except ValueError:
                return
        if addr and not groups["broadcast"]:
            return
        broadcast = broadcast and addr is None
        if is_query and broadcast:
            return _result(cmd_result, IcePAPError.CannotBroadCastQuery)
        if ack and broadcast:
            return _result(cmd_result, IcePAPError.CannotAcknowledgeBroadcast)
        args = map(str.strip, cmd[cmd_match.end() :].split())

        self._log.debug("processing args %r", cmd[cmd_match.end() :].split())
        if addr is None:
            func = getattr(self, instr, None)
            self._log.debug("processing func %r", func)
            if func is None:
                result = _result(cmd_result, IcePAPError.CommandNotRecognized)
            else:
                result = func(
                    cmd_result=cmd_result,
                    is_query=is_query,
                    broadcast=broadcast,
                    ack=ack,
                    args=args,
                )
        else:
            axis = self._get_axis(addr)
            if not isinstance(axis, Axis):
                result = _result(cmd_result, axis)
            else:
                func = getattr(axis, instr, None)
                self._log.debug("processing func %r", args)
                if func is None:
                    result = _result(cmd_result, IcePAPError.CommandNotRecognized)
                else:
                    result = _result(cmd_result, func(ack, *args))
        if is_query or ack:
            return result

    # --- pure system commands

    def move(self, args=(), cmd_result=None, **kwargs):
        result = []
        args = list(args)
        group = args[0].upper() == "GROUP"
        args = args[1:] if group else args
        axes, args = args[::2], args[1::2]
        logging.debug("Axes %r", axes)
        logging.debug("Args %r", args)
        for axis, arg in zip(axes, args):
            axis = self._get_axis(axis, system=True)
            if not isinstance(axis, Axis):
                return _result(cmd_result, axis)
            result.append(str(getattr(axis, "preparemove")(arg)))
        for axis, arg in zip(axes, args):
            axis = self._get_axis(axis, system=True)
            if not isinstance(axis, Axis):
                return _result(cmd_result, axis)
            result.append(str(getattr(axis, "startmove")(arg)))
        result = result[0]
        return _result(cmd_result, result)

    # def defposition(self, args=(), cmd_result=None, **kwargs):
    #            # import pdb; pdb.set_trace()
    #     group = args[0].upper() == "GROUP"
    #     args = map(int, args[1:] if group else args)
    #     axes_pos = []
    #     for i in range(0, len(args), 2):
    #         axis = self._get_axis(args[i])
    #         if not isinstance(axis, Axis):
    #             return _result(cmd_result, axis)
    #         axes_pos.append((axis, args[i + 1]))
    #     for axis, pos in axes_pos:
    #         self._log.debug("move %d to %d", axis.address, pos)
    #         setattr(axis, "_position", pos)
    #     return " OK"

    # --- system & axis commands but cannot be execute at system level for axis

    ver = read_command("ver", default=" 3.15")
    mode = read_command("mode", default="OPER")

    @read_command
    def help(self):
        return _build_help(self)

    # -- system & axes commands

    pos = posaxis = fpos = fposition = axes_command("pos", axes_arg_parser=two_string_command_parse_args)
    power = axes_command("power", axes_arg_parser=value_axes_parse_args)
    velocity = axes_command("velocity")
    acctime = axes_command("acctime")
    status = fstatus = axes_read_command("status")
    # movegroup = move = axes_command(move)
    movegroup = move = axis_command(move)
    abort = axes_command("abort", axes_arg_parser=axis_parse_args)
    stop = axes_command("stop", axes_arg_parser=axis_parse_args)

    # --- commands that in doc are reported as controller and axes but in
    #     fact can only be executed at axis level (ie: <axis>:...)
    #     tested with 3.14
    # vstatus = axes_read_command('vstatus')
    # stopcode = axes_read_command('stopcode')
    # vstopcode = axes_read_command('vstopcode')
    # alarm = axes_read_command('alarm')
    # warning = axes_read_command('warning')
    # wtemp = axes_command('wtemp')
