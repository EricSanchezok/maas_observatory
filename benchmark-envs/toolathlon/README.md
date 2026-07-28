# Toolathlon-Verified client runtime

The adapter pins the official Toolathlon repository at commit
`2aed2468858f15818acafa178518390cc4b0f5cb`. At execution time it creates an
ignored detached checkout under `benchmark-worktrees/`.

The default `self-hosted` backend expects an official Toolathlon evaluation
server prepared according to the pinned upstream README:

```bash
export TOOLATHLON_SERVER_HOST=your-server-host
export TOOLATHLON_SERVER_PORT=8080
export TOOLATHLON_SERVER_REVISION=2aed2468858f15818acafa178518390cc4b0f5cb
export TOOLATHLON_TASK_IMAGE_DIGEST=sha256:4d04fe4e0a6fdb4946f51bb05120cb44a0eef980231c11252f93b62897afcb9f
```

The upstream pull script at that commit names the mutable tag
`docker.io/lockon0927/toolathlon-task-image:1016beta`. On 2026-07-28, the
Docker Registry V2 `Docker-Content-Digest` response resolved that tag to:

```text
docker.io/lockon0927/toolathlon-task-image@sha256:4d04fe4e0a6fdb4946f51bb05120cb44a0eef980231c11252f93b62897afcb9f
```

Deploy the digest reference, not the mutable tag. The two environment values
are explicit operator attestations checked before a run; the client cannot
cryptographically inspect a remote server's container runtime.

The adapter invokes the official `eval_client.py`. Endpoint credentials are
passed through an allowlisted child environment and never appear in the OS
command line.
