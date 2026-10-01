# Public-source import boundary

The imported application baseline was independently compared with the running
bridge release and every currently served frontend asset. Old candidate working
directories were not used as the source of truth or modified.

Historical incident/review/release reports are not imported because some contain
private session identifiers and operational evidence. Current product specs and
contracts remain under docs. Existing private copies stay outside Git until their
operational retention obligations end; no extra archive copy was created.

Source changes for publication are intentionally narrow: the push-contact default
uses the public HTTPS site URI instead of personal email, and one test's human
session fixture is synthetic. Exact historical agent-test IDs in classification
policy are compatibility identifiers, not credentials or human conversation text;
removing them would change which records the app displays. No session contents,
credentials, account records, databases, or private deployment manifests are
included. Source SHA-256 constants are integrity checks, not API tokens.

The Gitleaks binary is version- and checksum-pinned. Its exact false-positive
fingerprints cover existing hashes, agent-test identifiers and intentionally fake
privacy-test inputs. New source and changed exceptions require renewed review.

Third-party Hermes patch source retains its MIT notice. No new license grant for
unrelated original application code is inferred solely from public hosting.

The README-only bootstrap created the main branch before protection could apply.
All application source is introduced through the migration PR. Test/review status
checks attest to the exact PR revision, not a moving branch or an older draft.
