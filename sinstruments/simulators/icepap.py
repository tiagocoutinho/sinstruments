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

from sinstruments.simulator import BaseDevice


MAX_AXIS = 128


def iter_axis(start=1, stop=MAX_AXIS + 1, step=1):
    start, stop = max(start, 1), min(stop, MAX_AXIS + 1)
    for i in range(start, stop, step):
        if i % 10 > 8:
            continue
        yield i


VALID_AXES = list(iter_axis())


def default_parse_args(icepap, query=True, broadcast=False, args=()):
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
            axes, args = args[::2], args[1::2]
    return axes, args


axis_value_parse_args = default_parse_args


def value_axes_parse_args(icepap, query=True, broadcast=False, args=()):
    if query:
        if broadcast:
            axes = sorted(icepap._axes.keys())
        else:
            axes, args = args, ()
    else:
        if broadcast:
            axes = sorted(icepap._axes.keys())
        else:
            axes = args[1:]
        args = len(axes) * [args[0]]
    return axes, args


def args_axes_parse_args(icepap, query=True, broadcast=False, args=()):
    if not query and not broadcast:
        axes, args = args[1:], args[:1]
    else:
        axes, args = default_parse_args(icepap, query, broadcast, args)
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
        # used directly as a decorator
        func, name = func_or_name, func_or_name.__name__
        if default is not None:
            ValueError(
                "Cannot give 'default' in method '{0}' " "decorator".format(name)
            )
    else:
        name = func_or_name
        attr_name = "_" + name
        if default is None:
            raise ValueError("Must give default string value")

        def func(self, value=None):
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
    NotPresent = 0
    NotResponsive = 1
    ConfigurationMode = 2
    Alive = 3


class DriverMode(enum.Enum):
    Oper = 0 << 2
    Prog = 1 << 2
    Test = 2 << 2
    Fail = 3 << 2


class DriverDisable(enum.Enum):
    PowerEnabled = 0 << 4
    NotActive = 1 << 4
    Alarm = 2 << 4
    RemoteRackDisableInputSignal = 3 << 4
    LocalRackDisableSwitch = 4 << 4
    RemoteAxisDisableInputSignal = 5 << 4
    LocalAxisDisableSwitch = 6 << 4
    SoftwareDisable = 7 << 4


class DriverIndexer(enum.Enum):
    Internal = 0 << 7
    InSystem = 1 << 7
    External = 2 << 7
    Linked = 3 << 7


class DriverReady(enum.Enum):
    NotReady = 0 << 9
    Ready = 1 << 9


class DriverMoving(enum.Enum):
    NotMoving = 0 << 10
    Moving = 1 << 10


class DriverSettling(enum.Enum):
    NotSettling = 0 << 11
    Settling = 1 << 11


class DriverOutOfWindow(enum.Enum):
    NotOutOfWindow = 0 << 12
    OutOfWindow = 1 << 12


class DriverWarning(enum.Enum):
    NotWarning = 0 << 13
    Warning = 1 << 13


class DriverStopCode(enum.Enum):
    EndOfMotion = 0 << 14
    Stop = 1 << 14
    Abort = 2 << 14
    LimitPos = 3 << 14
    LimitNeg = 4 << 14
    ConfiguredStop = 5 << 14
    Disabled = 6 << 14
    InternalFailure = 8 << 14
    MotorFailure = 9 << 14
    PowerOverload = 10 << 14
    DriverOverheading = 11 << 14
    CloseLoopError = 12 << 14
    ControlEncoderError = 13 << 14
    ExternalAlarm = 14 << 14


class LimitPos(enum.Enum):
    NotActive = 0 << 18
    Active = 1 << 18


class Axis(object):
    """IcePAP emulated axis"""

    def __init__(self, icepap, address=None, **opts):
        self.__icepap = weakref.ref(icepap)
        self.__motion = None
        self.__status = 0x00A00203
        self.address = address
        if address not in VALID_AXES:
            raise ValueError("{0} is not a valid address".format(address))
        self._log = logging.getLogger("{0}.{1}".format(icepap._log.name, address))
        for k, v in opts.items():
            setattr(self, "_" + k, v)
        self._name = opts.get("axis_name", "")

    @property
    def _icepap(self):
        return self.__icepap()

    active = axis_read_command("active", default="YES", cfg_info=bool)
    mode = axis_read_command("mode", default="OPER")
    status = fstatus = axis_read_command("status", default="0x00A00203")
    vstatus = axis_read_command("vstatus", default="TODO")
    stopcode = axis_read_command("stopcode", default="0x0002")
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
    power = axis_command("power", default="OFF")
    auxps = axis_command("auxps", default="ON")
    pos = fpos = axis_command("pos", default=0)
    enc = axis_command("enc", default=0)

    velocity = axis_command("velocity", default=1000)
    acctime = axis_command("acctime", default=0.25)

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
        # used directly as a decorator
        f, name = f_or_name, f_or_name.__name__
        if default is not None:
            ValueError(
                "Cannot give 'default' in method '{0}' " "decorator".format(name)
            )
    else:
        name = f_or_name
        attr_name = "_" + name

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
        is_query = kwargs["is_query"]
        if (is_query and "r" not in mode) or (not is_query and "w" not in mode):
            return IcePAPError.CommandNotRecognized

        args = kwargs["args"]
        broadcast = kwargs["broadcast"]
        cmd_result = kwargs["cmd_result"]

        # axis(es) command
        if axes_arg_parser:
            axes, args = axes_arg_parser(self, is_query, broadcast, args)
            result = []
            if is_query:
                for axis in axes:
                    axis = self._get_axis(axis, system=True)
                    if not isinstance(axis, Axis):
                        return _result(cmd_result, axis)
                    # TODO: handle errors
                    result.append(str(getattr(axis, name)()))
                result = " ".join(result)
            else:
                for axis, arg in zip(axes, args):
                    axis = self._get_axis(axis, system=True)
                    if not isinstance(axis, Axis):
                        return _result(cmd_result, axis)
                    # TODO: handle errors
                    result.append(getattr(axis, name)(arg))
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
    _INSTR = "(?P<instr>\\w+)"
    _CMD = re.compile(
        "{ack}\s*{addr}\s*{query}{instr}\s*".format(
            ack=_ACK, addr=_ADDR, query=_QUERY, instr=_INSTR
        )
    )

    def __init__(self, name, axes=None, **opts):
        super(IcePAP, self).__init__(name, **opts)
        axes_dict = {}
        for axis in axes or [dict(address=addr) for addr in iter_axis()]:
            axes_dict[axis["address"]] = Axis(self, **axis)
        self._axes = axes_dict
        for k, v in opts.items():
            setattr(self, "_" + k, v)

    @staticmethod
    def _cmd_result(cmd_match):
        """retrieve the command error message prefix from the command line"""
        groups = cmd_match.groupdict()
        # replace None values with ''
        groups_str = dict([(k, ("" if v is None else v)) for k, v in groups.items()])
        groups_str["instr"] = groups_str["instr"].upper()
        cmd_err = "{addr}{broadcast}{is_query}{instr}".format(**groups_str)
        return cmd_err

    def _get_axis(self, addr, system=False):
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

    def handle_message(self, line):
        self._log.debug("processing line %r", line)
        line = line.strip()
        responses = []
        for cmd in line.split(b";"):
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
        cmd = cmd.decode()
        cmd_match = self._CMD.match(cmd)
        if cmd_match is None:
            self._log.info("unable to parse command")
            cmd_result = cmd.replace("#", "").strip().split(" ", 1)[0]
            return _result(cmd_result, IcePAPError.CommandNotRecognized)
        groups = cmd_match.groupdict()
        ack, addr = groups["ack"], groups["addr"]
        broadcast, is_query = groups["broadcast"], groups["is_query"]
        instr = groups["instr"].lower()
        cmd_result = IcePAP._cmd_result(cmd_match)
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

        if addr is None:
            func = getattr(self, instr, None)
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
                if func is None:
                    result = _result(cmd_result, IcePAPError.CommandNotRecognized)
                else:
                    result = _result(cmd_result, func(*args))
        if is_query or ack:
            return result

    # --- pure system commands

    def move(self, args=(), cmd_result=None, **kwargs):
        #        import pdb; pdb.set_trace()
        group = args[0].upper() == "GROUP"
        args = map(int, args[1:] if group else args)
        axes_pos = []
        for i in range(0, len(args), 2):
            axis = self._get_axis(args[i])
            if not isinstance(axis, Axis):
                return _result(cmd_result, axis)
            axes_pos.append((axis, args[i + 1]))
        for axis, pos in axes_pos:
            self._log.debug("move %d to %d", axis.address, pos)
            setattr(axis, "_pos", pos)
        return cmd_result + " OK"

    # --- system & axis commands but cannot be execute at system level for axis

    ver = read_command("ver", default=" 3.15")
    mode = read_command("mode", default="OPER")

    @read_command
    def help(self):
        return _build_help(self)

    # -- system & axes commands

    pos = fpos = axes_command("pos")
    power = axes_command("power", axes_arg_parser=value_axes_parse_args)
    velocity = axes_command("velocity")
    acctime = axes_command("acctime")
    status = fstatus = axes_read_command("status")

    # --- commands that in doc are reported as controller and axes but in
    #     fact can only be executed at axis level (ie: <axis>:...)
    #     tested with 3.14
    # vstatus = axes_read_command('vstatus')
    # stopcode = axes_read_command('stopcode')
    # vstopcode = axes_read_command('vstopcode')
    # alarm = axes_read_command('alarm')
    # warning = axes_read_command('warning')
    # wtemp = axes_command('wtemp')
