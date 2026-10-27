# Sponsor integration setup

Offline sample mode needs Python 3.11+ only. Actual sponsor-backed investigation requires the real project path and the optional clients below. Keep local credentials, downloaded vendor bundles and runtime state out of version control.

## Meterless

Run `scripts/setup_meterless.ps1` with Git, Node and npm installed. It fetches the official [Meterless repository](https://github.com/Meterless/Meterless) at commit `1742dec56a66e58eef9dda2d2b865823758ca8cd`, retains upstream license files and installs the World Model reference dependencies with `npm ci --ignore-scripts`. The tested environment uses Node24.

The application bridge imports the upstream WorldModel class. Candidate facts, support links and owner reviews are projected into a local rebuildable graph; investigation reads this graph. Canonical events and owner reviews remain authoritative. This path is local reference software, not a hosted Meterless API, and does not require a Meterless API key.

## Zetaris

Obtain the platform bundle from the official [local download entry](https://cloud.enterprise.zetaris.com/dashboard/download) using your own authorized account and registry credentials. Follow the vendor's installation and verification instructions. The platform bundle, credential file and JDBC binaries are excluded from this source candidate. No license acceptance, registry account or fresh backend install is supplied by these files.

Install Java17 and the optional Python client packages with the following command. The verified bundle includes official driver `ndp-jdbc-driver-2.4.3.1-7eff043-driver.jar` and class `com.zetaris.lightning.jdbc.LightningDriver`.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-zetaris.txt
```

Configure the bundle's own `.env` as directed by Zetaris. Keep published ports on loopback. Resolve any occupied ports using the vendor-supported configuration; do not stop unrelated containers. The driver must be able to reach the dashboard source callback through `host.docker.internal` on the selected dashboard port. The dashboard's read-only callback route is available only for the active immutable snapshot lease.

After first scanning the chosen project and setting up both clients, start the web process with your own bundle path.

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m project_compass.cli --open 'D:\Projects\example-project' --web --port 8787 --meterless --zetaris-bundle 'D:\Tools\zetaris-platform'
```

The JDBC worker registers project-record and public-message REST families in a project-specific database, then retrieves the requested records through a federated `UNION ALL` query. The parent verifies IDs, exact payload bytes, SHA256 digests and family identity before returning the answer. Two source families do not imply two physical external databases. The UI reports the actual provider receipt.

Meterless provides the fact/support graph while Zetaris checks cited raw source retrieval. Required source IDs are gathered before retrieval, including any matched cached reason citations. A provider failure has no silent local fallback. Bounded subprocess timeouts and exact citation checks do not guarantee low latency or causal validity.

## Optional development tools and models

Codex is used for development and public-session intake. Cursor is optional and was not used for the current workflow. No NVIDIA contribution has been verified. Closed-source models and TypeSafe Jev are optional candidate suggestion paths, disabled by default. Any future request needs appropriate credentials, an explicit budget and an actual-usage log; unknown usage is not zero. This portable candidate includes no model credentials, private output cache or model usage ledger.
