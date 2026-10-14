# Glossary

Plain-English meanings, in alphabetical order.

**2.4 GHz**
One of the two Wi-Fi bands. The ESP32 sensor nodes only work on 2.4 GHz, not 5 GHz. Many
routers offer both. Nodes need the 2.4 GHz one.

**Admin / PIN**
The admin page (`/admin`) is where you set up Stagewatch. It is locked with a PIN of at
least 4 characters that you choose the first time. Everyone else can look at dashboards without it.

**Advisory tool**
Stagewatch tells you things. It does not control anything and is not a certified safety
system. See [Notices](integrations/README.md#notices).

**Dashboard**
A live page showing the site's numbers, graph, markers and alarms. Each one has its own
address, like `/d/foh`, and a layout for tablet, phone or wall screen.

**ESPHome**
A free tool that turns small boards (like the ESP32) into networked sensors. It builds
the program that goes on your node. Website: <https://esphome.io>.

**Firmware**
The program that runs on a node (or any small device).

**Flashing**
Copying firmware onto a node. The first time is done with a USB cable.

**GLOBCON**
DirectOut's software for controlling and monitoring its audio gear. Stagewatch can read the level meters of one GLOBCON controller. See [DirectOut GLOBCON](integrations/globcon.md).

**IP address**
A computer's address on your network, for example `192.168.1.50`. You type it in a browser
to reach Stagewatch: `http://192.168.1.50:8080`.

**Launcher**
A small helper that starts Stagewatch, restarts it if it crashes, and applies updates and
rollbacks. It is part of the managed install. You never run it yourself.

**Layer**
A GLOBCON controller groups channels into layers. The DirectOut GLOBCON card shows the channels of one layer.

**.local name / mDNS**
A way for devices on a network to find each other by name instead of by number, for
example `stagewatch.local`. Stagewatch and the nodes use it so they can find each other.
Some networks or devices block it. If a `.local` name does not work, use the IP address.

**Managed install**
The way the Windows and Raspberry Pi installers set things up. Stagewatch starts at boot,
restarts itself, is locked away from ordinary users, and can be updated from the admin page.

**Marker**
A bookmark in time, such as **Aligned** or **Headliner**. Stagewatch shows how much the air
(and so the sound travel time) has changed since each one.

**Nightly**
An update channel with the newest code, checked only by machines. Best tried away from a live show. See
**Stable**.

**Node**
A small Wi-Fi sensor box that measures the air and sends it to Stagewatch. Also called a
sensor node.

**Ontime**
Free software that runs a show's rundown, timer and clock. Stagewatch reads from it and never changes it. See [Ontime](integrations/ontime.md).

**PowerShell**
Windows' built-in command window. You paste a line, press Enter, and it runs.
Open it from the Start menu. "As Administrator" means allowed to change the computer.

**Repository / GitHub**
GitHub is a website where programmers keep their projects. A repository (or "repo") is one
project's folder there. Stagewatch's is at <https://github.com/MrFreekie/stagewatch>.
You don't need an account to download it.

**Role (Environment or Equipment)**
What a node or sensor is measuring. Environment is the air at the site and feeds the site
average. Equipment is gear, such as an amp rack, and is never averaged.

**Rundown**
The running order of a show in Ontime. The Ontime Rundown card shows which event is running and whether the show is ahead or behind.

**secrets.yaml**
A private text file next to a node's settings. It holds your Wi-Fi name and password and
the node's key. Keep it private.

**Sensor**
The part that measures something, such as a BME280 (temperature, humidity, pressure) or
an SHT45 (temperature, humidity).

**Site average**
The combined reading from all the sensors at the site. Sensors that have gone quiet are
left out, and odd ones out are ignored.

**Smaart**
Rational Acoustics' software for measuring sound. Stagewatch can show the sound level values it reports. See [Smaart](integrations/smaart.md).

**Stable**
The update channel of proper numbered releases. Use it for shows.

**Stale**
A reading that has not been updated for a while (60 seconds by default). It is greyed out
and left out of the site average.

**STEMMA QT**
Adafruit's name for a small plug-in cable and socket. It carries power and data, so you
can connect a sensor to a board with no soldering. It is the same as SparkFun's Qwiic.

**Terminal**
The Raspberry Pi's (and Linux's) command window. You paste a line, press Enter, and it runs.
The same idea as PowerShell on Windows.

**YAML**
A plain-text settings file format. Node settings are YAML files. Lines starting with `#`
are notes. Spaces at the start of lines matter, so don't add or remove them by accident.
