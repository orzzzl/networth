# Pairing the phone and connecting accounts

The owner's commands. Each runs in a terminal on `zelengs-macbook-air-2`
(`100.96.163.67`), from `~/networth-live` — a checkout the agents keep at the
commit that is installed on the host.

| Command | What it does | Spends a lifetime connection? |
|---|---|---|
| `scripts/pair-phone.sh` | prints a QR code and one line of text for the app's *Pair this phone* | no |
| `scripts/link.sh --sandbox` | the rehearsal: the same steps against Plaid's test bank | no |
| `scripts/link.sh` | connects **one** real institution | **yes — the moment the bank login finishes** |

## The order

1. **Pair first.** Tailscale on the phone must be connected. Run
   `scripts/pair-phone.sh`, scan the QR code with the phone's camera, copy the text
   it shows, and paste it into *Pair this phone* in the app. Within a minute or
   two the app shows `$0.00`, *no dated source for this total*, and when that was
   published — which is what the daemon publishes while nothing is connected, and
   proves the phone can reach it before anything irreversible happens. Running the
   command again re-pairs; the previous pairing stops working at once.
2. **Rehearse once.** `scripts/link.sh --sandbox` opens Plaid's page; choose any
   institution and log in as `user_good` / `pass_good`. Keep the terminal window
   open until it says `Connected`. Nothing real is connected. This is the only run
   of the steps *after* a login — exchange, account discovery, first sync — that
   can happen before a real connection depends on them, and no agent can do it: a
   completed login needs a person in a browser.
3. **Connect.** `scripts/link.sh`, type `LINK`, log in to one institution on the
   page that opens, and wait for `Connected` and the account count. Then run it
   again for the next institution.

## What `link.sh` does before a page exists

Every one of these refuses for free, because a connection is spent only when a
login finishes:

- this is the Mac the recovery copy belongs on;
- the daemon's services are running on the host;
- the host can count its lifetime connections and has one left;
- the backup path works end to end (Production only);
- the owner typed `LINK` at a terminal (Production only — an agent session has no
  terminal, so it cannot get past this);
- the request is minted, its recovery copy is written and read back on this Mac,
  and **the host has been seen polling that request** — not merely reported as
  running.

Only then is the page shown, with the time it stops working (30 minutes after the
mint).

## Several institutions

They can be connected back to back **only until the first page expires** — the
script prints that time after each success. After it, the host refuses to mint
until an agent has closed the expired request out (`RUNTIME.md`, *Close a
Production Link request after expiry*), and `link.sh` says so rather than failing.
That closure is the agents' job, takes a few minutes, and is needed after every
connection, finished or abandoned.

## When it stops

| It says | It means | Do |
|---|---|---|
| `Refused: …` / `Stopped before any page was shown` | no page existed, nothing can have been spent | read the reason; running again is safe |
| `…the host is not checking flow …` | the host would not have picked a login up, so no page was shown | tell the agents; running again will not help |
| `The page expired and no finished login was seen` | the host checked after expiry and saw none | tell the agents to close the flow before the next one |
| `STOP: …` | the host recorded something it could not finish, or could not check at all | **do not run it again**; tell the agents the flow id |

`scripts/link.sh --status <flow>` says where a flow stands without changing
anything, and `--resume <flow>` shows an unexpired page again without minting.

## For the agents

- After every `install-runtime.sh`, check the same commit out in
  `~/networth-live`. The driver and the host verbs are one program in two places.
- `pair-phone.sh` prints the payload key and refuses to run without a terminal.
  Do not work around that: a transcript would keep the key.
- There is no Sandbox pairing. The host keeps one payload key file for both
  environments, so pairing in Sandbox would overwrite the key the phone holds.
