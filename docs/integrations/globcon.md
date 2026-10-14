# DirectOut GLOBCON

**GLOBCON** is DirectOut's software for controlling and monitoring its audio gear. Stagewatch can
listen to one GLOBCON controller and show its level meters, channel names and mute state on the
[DirectOut GLOBCON card](../cards/globcon.md).

This page is for the person who sets Stagewatch up. It covers the connection. What the card shows is
on the card page.

Stagewatch is independent. It is not made, approved or supported by DirectOut. See the
[notices](README.md#notices).

> **Test status.** Controller names and layers have been checked on one real controller (the
> project owner's). **Live level meters with audio running have not yet been tested.** Please check
> the levels against GLOBCON's own screen before you rely on them.

## What Stagewatch does

- **It only listens.** Stagewatch cannot move a fader, mute, solo, change a layer, press a button or
  run a function in GLOBCON.
- **Levels are shown as GLOBCON reports them.** No averaging, smoothing or holding. Stagewatch does
  not say whether they are peak or RMS, because GLOBCON does not say. A channel with no signal shows
  an empty bar, never zero.
- **Mute is shown, never set.** A channel muted on the controller shows **MUTED** on the card.
- **Nothing is recorded.** The card is live only: no history and no markers. If GLOBCON goes quiet,
  the card freezes on the last levels and says how old they are.
- **It runs only while a dashboard has the card.** With the card on no dashboard, Stagewatch does not
  connect to GLOBCON.

### What Stagewatch sends

Stagewatch connects to GLOBCON's remote controller interface over a WebSocket (a live two-way web
connection) at `/api/v1` on the address and port you give. It sends only these kinds of message.
Every message passes one check before it leaves, and anything else is refused.

| Kind | What for | When |
|---|---|---|
| **Ping** | Asks "are you there?" | Every 2 seconds |
| **GET** and **SUBSCRIBE** | Ask for, and ask to be told about, the controller names and which controllers need a password | When it connects |
| **GET** and **SUBSCRIBE** | The strip labels, the layer, the layer names, whether each strip has a level meter, and mute state, for each controller a dashboard shows | When it connects, and when you add a controller to a dashboard |
| **SUBSCRIBE** | The level meter stream of each controller a dashboard shows (GLOBCON sends about 10 updates a second) | Same |
| **UNSUBSCRIBE** | "Stop telling me" about a controller no dashboard shows any more | When a card is removed |
| **AUTH** | Log in to a controller with the password you saved | **Only** if you saved a password and GLOBCON says that controller needs one. Once for each connection. |

GLOBCON's three other kinds of message, **SET**, **UPDATE** and **ACTION**, are the ones that move
faders, mute, solo, change layers and run functions. **They cannot be sent.** A test checks this.

If you save a password, it is sent only to log in. It crosses the show network as readable text, so
use a password you use nowhere else.

## Set up

1. Make sure GLOBCON is running.
2. Open **Admin** and find the **DirectOut GLOBCON** card. It is closed at first. Click its heading
   to open it.
3. Check **GLOBCON computer (127.0.0.1 is this computer)**. It starts as `127.0.0.1`. If GLOBCON
   runs on another computer, type that computer's address on your network.
4. Check **Port (9091 unless GLOBCON says otherwise)**. 9091 is where GLOBCON's remote controller
   page was on the machine we looked at. If you can open GLOBCON's remote controller page in a
   browser, the address in the browser's address bar shows the computer and the port.
5. If a controller needs a password, type it in **GLOBCON password (leave empty if GLOBCON has
   none)**. The saved password is never shown again. Tick **Remove the saved password** to delete it.
6. Click **Save**.
7. Under **User dashboards**, click **Edit cards** on a dashboard and tick **DirectOut GLOBCON**.
8. Next to the card, choose the **GLOBCON controller** (1 to 16) and **Channels shown**, then click
   **Save dashboards**. See [Dashboards](../admin/dashboards.md#globcon-controller-and-channels).

The address must be this computer or a computer on your own network. Public addresses are refused.

**What you'll see:** the line at the bottom of the Admin card changes from `Not running` to a
connected line and `Controller 1 in use`.

### If GLOBCON is on another computer: the Windows firewall

This is the most likely thing to go wrong. Windows blocks other computers from connecting to a
program unless the firewall allows it. **The firewall on the GLOBCON computer must allow the port
(9091 unless you changed it).** It caught the project owner out when GLOBCON ran on another PC.

When it is blocked, the Admin card says:
`Nothing answered at 192.168.1.50:9091. Check that GLOBCON is running and that the Windows firewall on its computer allows port 9091.`

Ask whoever looks after the GLOBCON computer to add a firewall rule that lets computers on your
show network connect to that port. You do not need a rule if GLOBCON and Stagewatch are on the same
computer.

## Controllers and layers

A GLOBCON controller has several layers, but it shows one at a time. Its strips and meters always
follow the layer it is on.

**Stagewatch cannot change the layer.** That would change what the engineer sees. So a card cannot
be set to "layer 2". The card's subtitle shows the controller's name and the layer's name as GLOBCON
reports them right now, so if someone changes the layer in GLOBCON, the subtitle and the channel
names change with it.

To show two layers on two screens, set up two controllers in GLOBCON and leave each on its own layer.
Then point each dashboard at one controller.

## How to check it worked

1. In Admin, open **DirectOut GLOBCON** and read the line at the bottom.
2. Open a dashboard that has the card.

**What you'll see:** the card shows the controller's name, the layer, and channels with names. The
bars move when audio plays. Compare a few levels with GLOBCON's own screen.

## If it didn't work

The Admin card says why, in plain words. The dashboard shows only short text with no address.

| What the Admin card says | What to do |
|---|---|
| `No GLOBCON address is saved yet…` | Type the computer GLOBCON runs on (`127.0.0.1` if it is this computer) and click **Save**. |
| `Nothing answered at … Check that GLOBCON is running and that the Windows firewall on its computer allows port …` | Check GLOBCON is running, then check the firewall on the GLOBCON computer (see above). |
| `The computer at … answered, but GLOBCON is not listening on port …` | Check GLOBCON is running and that the port is the one GLOBCON uses. |
| `Could not reach …. Check the address is spelt correctly and that this computer is on the same network as GLOBCON.` | Check the address and the network. |
| `The name … does not point to a computer on the local network…` | Use the address of the GLOBCON computer on your show network. |
| `Something answered at …, but it did not reply like GLOBCON's remote control.` | Check the address and port. |
| `Connected to …, but GLOBCON has sent nothing for 10 seconds…` | Check GLOBCON is still running and responding. |
| `GLOBCON at … closed the connection. Stagewatch will keep trying…` | Check GLOBCON has not been restarted or closed. |
| `GLOBCON at … sent more data than Stagewatch accepts…` | Check that this really is GLOBCON. |
| The card says **PASSWORD NEEDED** | GLOBCON wants a password for this controller. Enter it in Admin. |

Stagewatch keeps trying, waiting longer each time up to 30 seconds. When GLOBCON goes quiet or away,
a quiet notice appears in the alarm bar and clears when GLOBCON is back. It never makes a sound. See
[Alarm bar and notices](../cards/alarm-bar.md).

More: [Troubleshooting](../troubleshooting.md).

## Tested status

- **Checked on one real controller:** names and layers (the project owner's). **Also checked** against
  one recording of GLOBCON's own web page talking to a real GLOBCON.
- **Not yet tested:**
  - Live level meters with audio running.
  - Whether levels use the same scale on every type of channel. The card draws its bars from -72 dB to
    0 dB, as GLOBCON's own web page does.
  - What GLOBCON does when it is given a wrong password.
- **Tested on hardware: pending** for a full show day.

The card works in emulate mode (`--emulate`) with simulated controllers. GLOBCON is made by others
and can change. A new GLOBCON version may need a change to Stagewatch. The card is a convenience
view, not a metering standard, and not a way to run the mix.

Related: [Notices](README.md#notices), [Glossary](../glossary.md), [Troubleshooting](../troubleshooting.md).

Back to [Integrations](README.md).
