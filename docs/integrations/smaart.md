# Smaart sound level

**Smaart** is sound measurement software made by Rational Acoustics. Stagewatch can listen to a
running Smaart and show up to three of the sound level values it reports, with a timeline, on the
[Sound level card](../cards/sound-level.md).

This page is for the person who sets Stagewatch up. It covers the connection. What the card shows is
on the card page.

Stagewatch is independent. It is not made, approved or supported by Rational Acoustics. See the
[notices](README.md#notices).

> **Test status.** **Tested live on Smaart Suite 9.6.4.** Other Smaart versions are not tested.
> Port 26000 is the usual example: confirm it on your Smaart.

## What Stagewatch does

- **It listens.** Stagewatch connects to Smaart as a listen-only client and copies the numbers
  Smaart reports. It does not start or stop measuring, and never changes calibration, gain, logging,
  alarms or the mix.
- **Numbers are recorded exactly as Smaart sends them.** No averaging, smoothing, rounding or
  conversion. A value Smaart does not give, or marks as overload, shows as a dash. It is never shown
  as 0.
- **Up to three values**, each made of an **input** and a **value** (for example input
  `ASIO MADIface USB : Channel 7 (1)` and value `SPL A Slow`), chosen from Smaart's own lists.
- **It is not the Smaart developer kit (SDK).** Stagewatch uses the same web connection that Smaart's
  own SPL web page uses. Smaart can change that in a new version.
- **Sound levels are never part of the site average**, and cannot be given a calibration offset.
- **This is not a calibrated compliance record.** The Smaart log or report is the record that counts.

### What Stagewatch sends

Stagewatch sends Smaart **four fixed messages and nothing else**. Every message passes one check
before it leaves, and a message not on this list is refused. It never reads Smaart's history or
logs, so a gap in the record stays a gap.

| What it asks | Exact message | When |
|---|---|---|
| Is a password needed? | `{"action":"get"}` | When it connects |
| Which inputs are active, and which values exist? | `{"action":"get","target":"activeCalibratedInputs"}` | When it connects, then every 15 seconds (every 5 seconds while Smaart has no inputs), and when you click **Refresh** (at most once every 2 seconds) |
| Log in | `{"action":"set","properties":[{"password":"…"}]}` | **Only** if Smaart says a password is needed and you saved one |
| One update a second, please | `{"action":"set","properties":[{"targetFPS":1}]}` | When each meter connection opens (one for each input in use) |

The last one only sets the rate of that one meter connection. It does not change what Smaart
measures.

The connection is plain `ws://`, not encrypted, as far as we know. A password therefore crosses the
show network as readable text. Use a password that you use nowhere else.

## Set up Smaart

1. In Smaart, switch the API on: **Options → Preferences → API**.
2. If you want a password, set one there. You will need it below.
3. Start logging in Smaart, so Smaart has at least one active input.

## Set up Stagewatch

1. Open **Admin**. Under **Integrations**, in the **Connections** card, click **Add integration**
   and choose **Smaart**. (Already added? Click **Edit** on its row instead.)
2. Type the Smaart computer in **Address (IP or host name)**: `127.0.0.1` if Smaart runs on this
   computer.
3. Type the **Port**. Confirm it on your Smaart. Leave it empty and Stagewatch uses 26000.
4. If Smaart has an API password, type it in **Password**.
5. Click **Save**. Adding Smaart also switches recording on. The **Sound level (Smaart)** card now
   shows in Admin; click its heading to open it. **Record sound level** switches recording off and on.
6. Wait for the card to say it is connected. The input and value lists then fill with Smaart's own
   names.
8. For each of up to three values, choose **Value 1: input** and **Value 1: value** (then Value 2 and
   Value 3 if you want them). The input starts as `First input in Smaart`.
9. Click **Save** again.
10. Under **User dashboards**, click **Edit cards** on a dashboard, tick **Sound level**, and click
    **Save dashboards**. The card stays hidden until values are set up.

**What you'll see:** the line at the bottom of the Admin card says it is connected, not `Not
running`. The Sound level card shows your values.

### The address must be local

The address must be on your own network, or be this computer. Public addresses are refused. Names
that are really numbers, such as `134744072`, are refused too. A name is looked up each time
Stagewatch connects, and every answer must be a local address. If Smaart is on the same computer,
type `127.0.0.1` rather than a name.

### The password

- It is stored in Stagewatch's settings file, like an ESPHome encryption key.
- It is **never shown again**. The page only says `Saved`.
- It is never put in the logs or the diagnostics file.
- Leave the box empty to keep the saved password. Tick **Remove the saved password** to delete it.
- Saving a password, even the same one, makes Stagewatch log in again at once.
- A refused password is **not retried** until you save a new one. This stops Stagewatch locking
  Smaart's API with repeated wrong tries.

### If you rename an input in Smaart

Click **Refresh** under the drop-downs. Stagewatch asks Smaart for its lists again and keeps what
you chose where Smaart still lists it. A choice Smaart no longer lists stays chosen, is marked
`Smaart does not list this`, and shows as not available. It is never moved to another input.

### Locations and graph range

These change how the card looks. They never change what is recorded, and never restart the
connection.

- **Default location (shown on the card)**, and a **Location for this input** for each input once
  Smaart is connected (up to eight), for example `FOH` or `Stage left`. If every value has the same
  location, the card title reads `Sound level · FOH`. Otherwise each value shows its own.
- **Graph range**: **Automatic** fits the graph to what Smaart reports. **Custom** uses the **Min dB**
  and **Max dB** you type (0 to 200, at least 10 apart; it starts at 22 and 145). A reading outside
  a custom range is drawn at the edge with a small triangle, never hidden.

Input names appear on the dashboards. **Dashboards have no password**, so anyone on the show network
can read them.

### Which values you can choose

The values are named exactly as Smaart names them, for example `SPL A Slow`, `SPL C Slow`,
`LAeq 1` and `LAeq 10`. `FS Peak` is left out. Stagewatch cannot check what period an LAeq covers
beyond the name Smaart gives it.

A value saved by an older Stagewatch as `LAeq 15 min` now means Smaart's `LAeq 15`. If your Smaart
does not list that, the card says so and shows the value as not available, until you pick one that
Smaart does list.

Sound level has no limits or alarms.

## How to check it worked

1. In Admin, open **Sound level (Smaart)**.
2. Read the line at the bottom.

**What you'll see:** a connected line, then the source and input name. On the dashboard, each
value shows a number in dB.

## If it didn't work

The Admin card says why, in words.

| What the card says | What it means and what to do |
|---|---|
| `Smaart's API has a password. Enter it in the Sound level settings` | In **Connections**, click **Edit** on Smaart, type the password in **Password** and click **Save**. Stagewatch checks again once a minute. |
| `Smaart did not accept the password. Check it in the Sound level settings; Stagewatch will not try again until you save it` | Retype the password and click **Save**. |
| `Connected, but Smaart has no active inputs. Start logging in Smaart` | Start logging in Smaart. |
| `That address did not answer like Smaart's API. This build only knows the v4 API (/api/v4/)…` | Check the API is switched on in Smaart and the port is right. Stagewatch knows only Smaart's `/api/v4/` address. Another Smaart version may answer differently. |
| `Can't reach Smaart` | Nothing answered. Check Smaart is running, the address and port, and that both computers are on the same network. |
| `Smaart did not answer`, `Smaart closed the connection` | Check Smaart is still running. Stagewatch keeps trying, waiting longer each time up to 30 seconds. |
| `That name does not point to a computer on the local network` | Use the Smaart computer's address on your show network. |
| `Smaart is not connected` (when you click **Refresh**) | Wait until the card says it is connected. |
| `Refreshed too recently` | Wait 2 seconds and click **Refresh** again. |

More: [Troubleshooting](../troubleshooting.md).

## Tested status

- **Tested live** on Smaart Suite 9.6.4: the connection, the input and value lists, **Refresh**,
  inputs with brackets in their names, and locations.
- **Not tested:** other Smaart versions. Port 26000 is the usual example; confirm it on your Smaart.
- **Tested on hardware: pending** for a full show day.

Smaart is made by others and can change. A new Smaart version may need a change to Stagewatch.

Related: [Notices](README.md#notices), [Sound level card](../cards/sound-level.md),
[Troubleshooting](../troubleshooting.md).

Back to [Integrations](README.md).
