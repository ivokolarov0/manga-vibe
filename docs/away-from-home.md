# Reading away from home

[← Back to the README](../README.md)

> [!CAUTION]
> **Do not open port 8090 to the internet.** It's the easy way to read outside your home, and it's
> unsafe. MangaVibe is made for your home network only and has **no password or login**.

## Do this instead: use a VPN

A VPN lets your phone join your home network from anywhere, so MangaVibe stays private.

1. **Tailscale** (free, easiest). Install it on the NAS or PC that runs MangaVibe (on a Synology:
   **Package Center → Tailscale**) and on your phone, and sign in to the same account on both. Then
   open `http://<the NAS or PC's Tailscale name or address>:8090` on your phone, from anywhere.
   Nothing on your router changes.
2. **Your NAS's or router's VPN server**, for example Synology's **VPN Server** package (WireGuard
   or OpenVPN) or the VPN built into many routers. Connect to it on your phone, then open the same
   address you use at home.

## What "opening the port" means

You make MangaVibe reachable from anywhere through your home's internet address. People usually do
this by:

- adding a **port forwarding** rule for 8090 in their router
- putting the NAS or PC in the router's **DMZ**, or leaving **UPnP** on so apps open ports by themselves
- setting up a **reverse proxy** with a public address: Synology's *Login Portal → Reverse Proxy*,
  Nginx Proxy Manager, a Cloudflare tunnel without a login in front, or similar

## What happens if you do

Bots scan the whole internet for open ports all the time, so it will be found within days, even if
you never share the address. Anyone who finds it can:

- read and download your whole library, and see what you've been reading
- change or wipe reading progress, statuses, titles, descriptions, posters and matches
- disconnect plugins or break them, for example the MyAnimeList sync
- slow down or overload your NAS or PC by starting scans over and over
- reach any bug in MangaVibe or the packages it uses, straight from the internet

Anyone who reaches it can also use **Delete series**, which deletes the series' files. If you open
MangaVibe to the internet, mount the manga folder read-only (`:ro`) so your files stay safe. The login
details plugins save, such as MyAnimeList's, are never sent to the browser. **Everything else is open
to anyone.**

## Not sure if you opened it by accident?

Check your router's *Port forwarding* (or *Virtual server*) list for 8090 and remove it, turn UPnP
off if you don't need it, and remove any reverse proxy rule that points at port 8090.
