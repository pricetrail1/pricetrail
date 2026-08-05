# One real record, and what getting it proved

Everything in `--demo` mode is invented. This file is the opposite: one vendor,
checked by hand against primary sources on 4 August 2026. It is here as a
reference for what real output should look like, not as part of the archive.

## Help Scout, verified 2026-08-04

From Help Scout's own billing documentation (updated 5 June 2026), which states
the Standard plan price directly in a proration worked example:

| Field | Value | Source |
|---|---|---|
| Standard | $25 / user / month | helpscout.com billing docs |
| Plus | $45 / user / month | third-party, unconfirmed |
| Pro | $75 / user / month | third-party, unconfirmed |
| Extra inbox | $12/mo monthly, $10/mo annual | helpscout.com billing docs |
| Extra Docs site | $20 / site / month | helpscout.com billing docs |
| AI Answers | charged per resolution | helpscout.com billing docs |
| Currency | USD | helpscout.com billing docs |
| Non-profit discount | 10% | helpscout.com billing docs |

Only the rows marked as coming from Help Scout's own site should be treated as
facts. The other two are what third parties claim, which is exactly the
category of information this project exists to replace.

## What looking for this data actually turned up

Searching for one company's current pricing returned eight sources published
between September 2025 and June 2026. Their claims for the entry plan:

- $22 per user per month
- $20 per user per month billed annually
- $25 per user per month
- $25 / $45 / $75, described as verified against the live page

Several also noted that Help Scout changed its billing model outright during
this period, from charging by contacts to charging by user, and that some
customers remain on legacy plans that are no longer offered.

Three things follow from that, and they are the argument for this whole
project:

1. **The data is not sitting somewhere waiting to be collected.** If a clean,
   current, trustworthy record of software pricing existed, those eight
   articles would agree. They do not.
2. **Secondhand sources go stale silently.** An article confidently stating
   $22 is not marked as wrong once the price moves. It just sits there,
   ranking, being read.
3. **History is the part nobody has.** Every source above describes *now*.
   None can tell you when the model changed, what it replaced, or what the
   price was eighteen months ago — because nobody was writing it down.

## The uncomfortable implication

You cannot download your way to this dataset, and neither can a competitor.
The only way to have it is to start recording and keep recording. That is the
moat, and it is also why there is nothing to hand you today except this one
row.

Start the crawler. In six months you will have something that does not
currently exist.
