# Third-party notices

ProjectCompass does not assign a new license to upstream work or to its own
original source through this notice file.

## Devpost Learn AI Basics Skill Pack

Local workflow copies under `.agents/skills/` and `agent/skills/` originate from
[challengepost/learn-ai-basics](https://github.com/challengepost/learn-ai-basics).
The source/file hashes are recorded in `skills-lock.json`. The upstream MIT
license, including its copyright and permission notice, is preserved in
[licenses/learn-ai-basics-MIT.txt](licenses/learn-ai-basics-MIT.txt).
These skills are build-process tools and are not needed to run the application.

## Meterless

The optional setup script fetches Meterless at pinned commit
`1742dec56a66e58eef9dda2d2b865823758ca8cd`. The imported World Model reference is
upstream Apache-2.0 code. Setup retains its repository and component license
files under the ignored local checkout. `scripts/meterless_bridge.mts` is the
ProjectCompass adapter; the upstream source is not vendored into this repository.

## Zetaris and JDBC clients

Obtain the platform and official JDBC driver through the authorized Zetaris
download flow. Vendor archives, binaries and registry credentials are not included
in this repository. The setup guide references the user's own authorized bundle.

Optional Python packages are installed from the pinned requirements file.
Installed metadata identifies JayDeBeApi1.2.3 as GNU LGPL, JPype1.6.0 as Apache,
and packaging25.0 with Apache/BSD classifications. Their upstream distributions
carry their own notices; no package source or binary is redistributed here.
