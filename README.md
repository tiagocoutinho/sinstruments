# Instrument Simulator

![Pypi python versions][pypi-python-versions]
![Pypi version][pypi-version]
![Pypi status][pypi-status]
![License][license]

A simulator for real hardware. This project provides a server able to spawn
multiple simulated devices and serve requests concurrently.

This project provides only the required infrastructure to launch a server from
a configuration file (YAML, TOML or json) and a means to register third-party device plugins through the python entry point mechanism.

So far, the project provides transports for TCP, UDP and serial line.
Support for new transports (ex: USB, GPIB or SPI) is being implemented on a
need basis.

PRs are welcome!

## Installation

(**TL;DR**: `pip install sinstruments[all]`)

From within your favorite python environment:

```
$ pip install sinstruments
```

Additionally, if you want to write YAML configuration files in YAML:

```
$ pip install sinstruments[yaml]
```

...or, for TOML based configuration:

```
$ pip install sinstruments[toml]
```

## Execution

Once installed the server can be run with:

```
$ sinstruments-server -c <config file name>
```

The configuration file describes which devices the server should instantiate
along with a series of options like the transport(s) each device listens for
requests on.

### Example

Imagine you need to simulate 2 [GE Pace 5000](https://github.com/tiagocoutinho/gepace)
reachable through a TCP port each and a [CryoCon 24C](https://github.com/tiagocoutinho/cryocon) accessible through the serial line.

First, make sure the dependencies are installed with:
```
$ pip install gepace[simulator] cryoncon[simulator]
```

Now we can prepare a YAML configuration called `simulator.yml`:

```YAML
devices:
- class: Pace
  name: pace-1
  transports:
  - type: tcp
    url: :5000
- class: Pace
  name: pace-2
  transports:
  - type: tcp
    url: :5001
- class: CryoCon
  name: cryocon-1
  transports:
  - type: serial
    url: /tmp/cryocon-1
```

We are now ready to launch the server:
```
$ sinstruments-server -c simulator.yml
```

That's it! You should now be able to connect to any of the Pace devices through
TCP or the CryoCon using the local emulated serial line.

Let's try connecting to the first Pace with the *nc* (aka netcat) linux command
line tool and ask for the well known `*IDN?` SCPI command:

```
$ nc localhost 5000
*IDN?
GE,Pace5000,204683,1.01A
```

## Device catalog

This is a summary of the known third-party instrumentation libraries which
provide their own simulators.


* [cryocon](https://github.com/tiagocoutinho/cryocon)
* [fast-spinner](https://github.com/tiagocoutinho/fast-spinner)
* [gepace](https://github.com/tiagocoutinho/gepace)
* [icepap](https://github.com/ALBA-Synchrotron/pyIcePAP)
* [julabo](https://github.com/tiagocoutinho/julabo)
* [vacuubrand](https://github.com/tiagocoutinho/vacuubrand)
* [xia-pfcu](https://github.com/tiagocoutinho/xia-pfcu)
* Mythen detector (from Dectris) - not publicly available yet

If you wrote a publicly available device feel free complete the above list by
creating a PR.

*Hint*: `sinstruments-server ls` shows a list of available plugins.


## Configuration

The configuration file can be a YAML, TOML or JSON file as long as it translates to a dictionary with the description given below.

In this chapter we will use YAML as a reference example.

The file should contain at least a top-level key called `devices`.
The value needs to be a list of device descriptions:

```YAML
devices:
  - class: Pace
    name: pace-1
    transports:
    - type: tcp
      url: :5000
```

Each device description must contain:

* **class**: Each third-party plugin should describe which text
  identify itself
* **name**: a unique name. Each device must be given a unique name at
  your choice
* **transports**: a list of transports from where the device is accessible.
  Most devices provide only one transport.
  * **type**: Each transport must define its type (supported are `tcp`, `udp`, `serial`)
  * **url**: the url where the device is listening on

Any other options given to each device are passed directly to the specific
plugin object at runtime. Each plugin should describe which additional options
it supports and how to use them.

### TCP and UDP

For TCP and UDP transports, the **url** has the `<host>:<port>` format.

An empty host (like in the above example) is interpreted as `0.0.0.0` (which
means listen on all network interfaces). If host is `127.0.0.1` or `localhost`
the device will only be accessible from the machine where the simulator is
running.

A port value of 0 means ask the OS to assign a free port (useful for running
a test suite). Otherwise must be a valid TCP or UDP port.

### Serial line

The **url** represents a special file which is created by the simulator to
simulate a serial line accessible like a `/dev/ttyS0` linux serial line file.

This feature is only available in linux and systems for which the pseudo
terminal `pty` is implemented in python.

The **url** is optional. The simulator will always create a non deterministic
name like `/dev/pts/4` and it will log this information in case you need to
access. This feature is most useful when running a test suite.

You are free to choose any **url** path you like (ex: `/dev/ttyRP10`) as long
as you are sure the simulator has permissions to create the symbolic file.

### Simulating communication delays

For any of the transports (TCP, UDP and serial line) is is possible to do basic
simulation of the communication channel speed by providing an additional
`baudrate` parameter to the configuration. Example:

```YAML
- class: CryoCon
  name: cryocon-1
  transports:
  - type: serial
    url: /tmp/cryocon-1
    baudrate: 9600
```


### Back door

The simulator provides a gevent back door python console which you can activate
if you want to access a running simulator process remotely. To activate this
feature simply add to the top-level of the configuration the following:

```YAML
backdoor: ["localhost": 10001]
devices:
  - ...
```

You are free to choose any other TCP port and bind address. Be aware that this
backdoor provides no authentication and makes no attempt to limit what
remote users can do. Anyone that can access the server can take any action that
the running python process can. Thus, while you may bind to any interface, for
security purposes it is recommended that you bind to one only accessible to the
local machine, e.g., 127.0.0.1/localhost.

**Usage**

Once the backdoor is configured and the server is running, in a another
terminal, connect with:

```
$ nc 127.0.0.1 10001
Welcome to Simulator server console.
You can access me through the 'server()' function. Have fun!
>>> print(server())
...
```

## Develop a new simulator

Writting a new device is simple. Let's imagine you want to simulate a SCPI
oscilloscope. The only thing you need to do is write a class inheriting
from BaseDevice and implement the `handle_message(self, message)` where you
should handle the different commands supported by your device:


```python
# myproject/simulator.py

from sinstruments.simulator import BaseDevice

class Oscilloscope(BaseDevice):

    def handle_message(self, message):
        self._log.info("received request %r", message)
        message = message.strip().decode()
        if message == "*IDN?":
            return b"ACME Inc,O-3000,23l032,3.5A"
        elif message == "*RST":
            self._log.info("Resetting myself!")
        ...
```

Don't forget to always return `bytes`! The simulator doesn't make any guesses
on how to encode `str`

Assuming this file `simulator.py` is part of a python package called `myproject`,
the second thing to do is register your simulator plugin in your setup.py:

```python
setup(
    ...
    entry_points={
        "sinstruments.device": [
            "Oscilloscope=myproject.simulator:Oscilloscope"
        ]
    }
)
```

You should now be able to launch your simulator by writing a configuration
file:

```YAML
# oscilo.yml

devices:
- class: Oscilloscope
  name: oscilo-1
  transports:
  - type: tcp
    url: :5000
```

Now launch the server with
```
$ sinstruments-server -c oscillo.yml
```

and you should be able to connect with:

```
$ nc localhost 5000
*IDN?
ACME Inc,O-3000,23l032,3.5A
```

### Configuring message terminator

By default the `eol` is set to `\n`. You can change it to any character with:

```python
class Oscilloscope(BaseDevice):

    newline = b"\r"

```

### Request with multiple answers

If your device implements a protocol which answers with multiple (potentially
delayed) answers to a single request, you can support this by
converting the `handle_message()` into a generator:

```python
class Oscilloscope(BaseDevice):

    def handle_message(self, message):
        self._log.info("received request %r", message)
        message = message.strip().decode()
        if message == "*IDN?":
            yield b"ACME Inc,O-3000,23l032,3.5A"
        elif message == "*RST":
            self._log.info("Resetting myself!")
        elif message == "GIVE:ME 10":
            for i in range(1, 11):
                yield f"Here's {i}\n".encode()
        ...
```
Don't forget to always yield `bytes`! The simulator doesn't make any guesses
on how to encode `str`

### Support for specific configuration options

If your simulated device requires additional configuration, it can be supplied
through the same YAML file.

Let's say you want to be able to configure if your device is in `CONTROL` mode
at startup. Additionally, if no initial value is configured, it should default
to 'OFF'.

First lets add this to our configuration example:

```YAML
# oscilo.yml

devices:
- class: Oscilloscope
  name: oscilo-1
  control: ON
  transports:
  - type: tcp
    url: :5000
```

Then, we re-implement our Oscilloscope `__init__()` to intercept this new
parameter and we handle it in `handle_message()`:

```python
class Oscilloscope(BaseDevice):

    def __init__(self, name, **opts):
        self._control = opts.pop("control", "OFF").upper()
        super().__init__(name, **opts)

    def handle_message(self, message):
        ...
        elif message == "CONTROL":
            return f"CONTROL {self._control}\n".encode()
        ...
```

You are free to add as many options as you want as long as they don't conflict
with the reserved keys `name`, `class` and `transports`.

### Writing a specific message protocol

Some instruments implement protocols that are not suitably managed by a EOL
based message protocol.

The simulator allows you to write your own message protocol. Here is an example:

```python
from sinstruments.simulator import MessageProtocol


class FixSizeProtocol(MessageProtocol):

    Size = 32

    def read_messages(self):
        transport = self.transport
        buff = b''
        while True:
            buff += transport.read(self.channel, size=4096)
            if not buff:
                return
            for i in range(0, len(buff), self.Size):
                message = buff[i:i+self.Size]
                if len(message) < self.Size:
                    buff = message
                    break
                yield message


class Oscilloscope(BaseDevice):

    protocol = FixSizeProtocol

    ...
```

## Pytest fixture

If you are developing a python library that provides access to an instrument
accessible through socket or serial line and you wrote a simulator for it, you
might be interested in testing your library against the simulator.

sinstruments provides a pair of pytest helpers that spawn a simulator in a
separate thread.

### `server_context`
The first usage is simply using the `server_context` helper.
There is actually nothing pytest speficic about this helper so you could
imagine using it in other scenarios as well.

Here is an example:

```python
import pytest

from sinstruments.pytest import server_context

cfg = {
    "devices": [{
        "name": "oscillo-1",
        "class": "Oscilloscope",
        "transports": [
            {"type": "tcp", "url": "localhost:0"}
        ]
    }]
}

def test_oscilloscope_id():
    with server_context(cfg) as server:
        # put here code to perform your tests that need to communicate with
        # the simulator. In this example an oscilloscope client
        addr = server.devices["oscillo-1"].transports[0].address
        oscillo = Oscilloscope(addr)
        assert oscillo.idn().startswith("ACME Inc,O-3000")
```

You might notice that in the configuration we use port `0`. This is telling
the simulator to listen on any free port provided by the OS.

The actual test retrieves the current address assigned by the OS and uses it in
the test.

As you can see, the tests are not dependent of the availability of one specific
port which makes them portable.

Here is a suggestion on how you could write your own fixture using the
`server_context` helper. The aim was to reduce the amount of boilerplate
code you need to write your test:

```python
@pytest.fixture
def oscillo_server():
    with server_context(config) as server:
        server.oscillo1 = server.devices["oscillo-1"]
        server.oscillo1.addr = server.oscillo1.transports[0].address
        yield server


def test_oscilloscope_current(oscillo_server):
    oscillo = Oscilloscope(oscillo_server.oscillo1.addr)
    assert .05 < oscillo.current() < 0.01
```

### `server`

A second helper is the `server` fixture. This fixture depends on an existing
`config` feature that must be present in your module. Here is an example
following the previous code:

```python
from sinstruments.pytest import server

@pytest.fixture
def config()
    yield cfg

def test_oscilloscope_voltage(server):
    addr = server.devices["oscillo-1"].transports[0].address
    oscillo = Oscilloscope(addr)
    assert 5 < oscillo.voltage() < 10
```

[pypi-python-versions]: https://img.shields.io/pypi/pyversions/sinstruments.svg
[pypi-version]: https://img.shields.io/pypi/v/sinstruments.svg
[pypi-status]: https://img.shields.io/pypi/status/sinstruments.svg
[license]: https://img.shields.io/pypi/l/sinstruments.svg
