# ChunkWise CSE

Monorepo for the ChunkWise CSE project. Each component lives in its own folder and has two long-lived branches:

| Component | Folder | Development branch | Production branch |
|---|---|---|---|
| SLM benchmarking | `slm-benchmarking/` | `slm-benchmarking/dev` | `slm-benchmarking/production` |

Work is merged into a component's `dev` branch through pull requests, and promoted to its `production` branch once stable.
