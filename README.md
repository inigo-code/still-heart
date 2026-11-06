# Still Heart

Portable bulk picture / album downloader. Paste a gallery URL, **Scan** to pick stills (Bulk Image Downloader style), or **Grab** the whole album with [gallery-dl](https://github.com/mikf/gallery-dl).

## Run

### Portable desktop app

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build.ps1
```

That produces **one file**: `dist\StillHeart.exe`. Copy it anywhere and double-click. gallery-dl is inside. First launch can take 15–30 seconds while it unpacks. The app then keeps `data\`, `downloads\`, and `stillheart.log` in a `StillHeart` folder next to the exe. If the exe is already inside a folder named StillHeart, it uses that folder.

Windows needs [WebView2](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) (already on Windows 11 and most Windows 10).

### From source

```powershell
.\start.ps1
```

## Use

1. Paste one or more gallery / album / profile URLs.
2. **Scan** lists the files in a thumbnail grid — tick what you want, then download (the BID workflow).
3. **Grab** skips preview and saves the whole set.

Login walls (Instagram, Pixiv, X, etc.): open Settings, then Accounts, and sign in from inside the app. If a site refuses that, export a Netscape `cookies.txt` or pick your browser under Settings.

## What it improves on BID

- **300+ sites** via gallery-dl (albums, profiles, and posts — not just the current page’s `<img>` tags)
- **Scan then pick**, or skip preview and grab immediately
- **Cookies** from a `cookies.txt` file or Chrome / Edge / Firefox
- **Skip already downloaded** files with a local archive
- **Folder / filename templates**, type filters, min width, range (`1-80`)
- **Concurrent albums**, pause between requests, optional speed limit
- **History, library, and a searchable site list**
- **One portable exe** — no installer

## Legal

You must have the right to save the files. Still Heart does not bypass paywalls, DRM, or site bans.

## Stack

Python / FastAPI on `127.0.0.1` only, gallery-dl, optional ffmpeg, WebView2 window.
