# Networking

How to connect Stagewatch, its sensor nodes and your tablets on a show network.

> **Not yet tested as a whole.** The commands on this page are standard Raspberry Pi OS and
> Debian commands, but this layout has not been tried end to end with Stagewatch yet. Try it
> at home before a show, and keep a way back (a screen and keyboard on the Pi).

## The simple way

Put everything on **one network**: the Stagewatch computer, the sensor nodes and the tablets.
This is what the install guides assume, and it is fine for most shows. Sensor nodes need
**2.4 GHz** Wi-Fi.

## The recommended show layout (Raspberry Pi)

Keep the sensor nodes off your control network:

```
 Sensor nodes ))) Wi-Fi )))  Raspberry Pi  ---- Ethernet ----  Control network
 (2.4 GHz, own network)      (Stagewatch)                     (tablets, consoles, Ontime)
```

- The **nodes** join a small Wi-Fi network of their own (a cheap travel router or access point).
  The Pi joins it as a Wi-Fi client.
- The Pi's **Ethernet** is on your control network. Tablets open the dashboards from there.
- There is **no routing** between the two. Nothing on the Wi-Fi can reach the control network
  through the Pi.

Why: the nodes are small Wi-Fi gadgets. Keeping them on their own network means one of them
can never see or disturb your audio or lighting network.

### 1. Give every device a fixed address

In the router for each network, set a **DHCP reservation** for:

- the Pi (on both networks),
- each sensor node,
- anything Stagewatch reads, such as Ontime, a Shelly meter or a GLOBCON controller.

Fixed addresses mean Stagewatch can always find them, even if name look-up (`.local`) fails.

### 2. Stop the Pi sending its traffic out over the Wi-Fi

The Pi must use the Ethernet for everything except the nodes. Find the Wi-Fi connection's
name:

```bash
nmcli connection show
```

Then, with that name in place of `NODES-WIFI`:

```bash
sudo nmcli connection modify "NODES-WIFI" ipv4.never-default yes ipv6.never-default yes 802-11-wireless.powersave 2
sudo nmcli connection up "NODES-WIFI"
```

- `never-default` stops the Wi-Fi becoming the Pi's way to the internet.
- `powersave 2` turns Wi-Fi power saving **off**, so readings do not drop out.

### 3. Make sure the Pi does not route between the networks

This is off on a normal install. Check it:

```bash
sysctl net.ipv4.ip_forward
```

You should see `net.ipv4.ip_forward = 0`. If you see `1`, something else on the Pi has turned
routing on; find out what before the show.

### 4. Allow nothing in from the node Wi-Fi

Stagewatch connects **out** to the nodes, so nothing needs to connect **in** over the Wi-Fi.
Here `wlan0` is the Wi-Fi and `eth0` the Ethernet (check with `ip addr`):

```bash
sudo apt install ufw
sudo ufw allow in on eth0 to any port 22 proto tcp
sudo ufw allow in on eth0 to any port 8080 proto tcp
sudo ufw allow in on eth0 to any port 5353 proto udp
sudo ufw deny in on wlan0
sudo ufw enable
```

Port 22 is SSH (leave it out if you do not use it), 8080 the dashboards, 5353 the `.local`
names. If you changed Stagewatch's port when you installed it, use that number instead of
8080.

**Check it worked:** a tablet on the control network still opens the dashboards, and the
sensor readings still arrive.

## Ports Stagewatch uses

| Port | Direction | What for |
|---|---|---|
| 8080 TCP | in | Dashboards and admin page (or the port you chose) |
| 5353 UDP | in and out | `.local` names (mDNS) |
| 6053 TCP | out, to nodes | ESPHome sensor nodes |
| 4001 TCP | out | Ontime (default port) |
| 80 TCP | out | Shelly meters |

Other integrations use the address and port you enter in Admin.

## Never on the internet

Run Stagewatch on a private show network only. Do not forward its port from a router to the
internet. See [Security and data](admin/security-and-data.md).
