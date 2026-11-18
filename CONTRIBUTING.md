# Contributing

## Contributor license agreement

Each contribution to Benchhand needs a signed [Contributor License Agreement](CLA.md) (CLA). The CLA is the Individual Contributor Non-Exclusive License Agreement from the Contributor Agreements chooser. You keep the copyright in your contribution. You grant Sjors Robroek a license to use it and to license it to others.

### How to sign

When you open your first pull request, the `CLA` check fails and lists each commit author who has not signed. To sign, post this comment on the pull request, with no other text:

```text
I have read the Benchhand CLA version ca-individual-nonexclusive-7b15a98-v1 and I hereby sign it.
```

The bot `sjorsr-cla-bot` records the signature in [benchhand/cla-signers](https://github.com/benchhand/cla-signers) and runs the check again. A signature covers every later pull request from you, until the CLA version changes.

The check matches each commit to a GitHub account through the commit email. If a commit email has no GitHub account, add that email to your GitHub account. Then push again.

To withdraw a signature, edit or delete the signing comment. After that, the check fails on your open pull requests until you sign again.

### Contributions from your employer

If your employer has rights in work that you create, get its permission first. The "How to use this Contributor Agreement" section of the CLA describes this. Benchhand has no CLA for companies.

### Bots

These accounts do not sign:

- `dependabot[bot]`
- `renovate[bot]`
- `github-actions[bot]`
- `sjorsr-release-bot[bot]`, the release bot
- `sjorsr-cla-bot[bot]`, the CLA bot

### Unsigned pull requests

The `CLA` check runs on each pull request and covers each author of each commit in it. The merge queue merges a pull request only after the check passes. A pull request with an unsigned author stays open until that author signs or a maintainer removes the commits.

### Which check decides

The merge queue runs the `CLA` check again on each merge group. That run reads the current signatures for every pull request in the group. A merge group passes only when every author in it has a current signature. This check decides whether a contribution merges.

The `CLA` check on a pull request can show a stale result for a short time in these cases:

- two comment events for the same signature arrive out of order
- two pull requests share one head commit and change while the check runs
- a contributor withdraws a signature while more than 2000 pull requests are open

To get a fresh result on a pull request, push a new commit or post the signing comment again.
