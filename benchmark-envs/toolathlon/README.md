# Toolathlon-Verified client runtime

The adapter pins the official Toolathlon repository at commit
`2aed2468858f15818acafa178518390cc4b0f5cb`. At execution time it creates an
ignored detached checkout under `benchmark-worktrees/`.

The default `self-hosted` backend expects an official Toolathlon evaluation
server prepared according to the pinned upstream README:

```bash
export TOOLATHLON_SERVER_HOST=your-server-host
export TOOLATHLON_SERVER_PORT=8080
```

The adapter invokes the official `eval_client.py`. Endpoint credentials are
passed through an allowlisted child environment and never appear in the OS
command line.
