# CI direction

The earlier standalone ZooKeeper/HDFS companion Notebook experiments establish
that QEMU can run the services, but do not verify the repository's original
Notebook procedures. They are not CI execution targets.

The current implementation operates the original Notebook working copies through
LC4RI's browser UI. See [tests/lc4ri/README.md](../tests/lc4ri/README.md) for the
exact HDFS/YARN/Spark scope, prerequisites, input boundaries and evidence.
