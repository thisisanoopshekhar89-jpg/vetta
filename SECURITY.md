# Security

## Reporting a vulnerability

Email **thisisanoopshekhar89@gmail.com**. Please do not open a public issue for a
security problem.

Useful to include: what you did, what happened, and a synthetic file that
reproduces it. Never send a real person's résumé — that is personal data.

## What counts as a security issue here

- A way to make Vetta **miss** hidden text or an injection attempt. This is the
  most valuable report you can send, because a missed payload is the tool failing
  at its one job.
- A crafted document that causes a crash, hang, or unbounded resource use.
- Path traversal, or anything that reads or writes outside the intended folders.
- A way to make the desktop app leak a résumé off the machine. It is designed to
  make no network calls at all.

## What Vetta is not

It is not a lie detector. It reports manipulation of a *document*, not the truth of
the claims inside it. A `review` verdict is a prompt for a human to look more
closely, never an accusation, and output must not be the sole basis for a hiring
decision.

## Handling résumés

Résumés are personal data. The desktop app writes uploads to a temporary folder for
the duration of a scan and deletes them immediately afterwards, and makes no network
requests. A workspace database does store extracted text and findings — keep it only
as long as your obligations allow.
