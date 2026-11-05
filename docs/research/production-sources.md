# Production research notes

Checked 2026-11-05. This is a curated reading and source-code list, not an endorsement, benchmark or survey of hiring demand. Product docs change; lock the selected SDKs and test the exact deployed versions. No real credentials, resumes or internal incident material were sent to public sources.

| Source | What to learn / how it affects this design |
|---|---|
| [LangChain recursive splitter](https://docs.langchain.com/oss/python/integrations/splitters/recursive_text_splitter) | Use library Document/splitting mechanics; make length units and overlap explicit |
| [Upstream recursive splitter code](https://github.com/langchain-ai/langchain/blob/master/libs/text-splitters/langchain_text_splitters/character.py) | Read recursive separators/merging; do not maintain a second implementation |
| [Upstream base splitter code](https://github.com/langchain-ai/langchain/blob/master/libs/text-splitters/langchain_text_splitters/base.py) | Read split_documents/create_documents and start-index behavior; inspect the installed locked version too |
| [Celery task semantics](https://docs.celeryq.dev/en/stable/userguide/tasks.html) | Acknowledgements, retries and worker-loss caveats require idempotent effects |
| [Celery optimization](https://docs.celeryq.dev/en/stable/userguide/optimizing.html) | Prefetch and separating long/short tasks; queue growth is a capacity problem |
| [Celery routing](https://docs.celeryq.dev/en/stable/userguide/routing.html) | Named queues and transport-dependent priority; fairness remains an application policy |
| [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) and [Alembic async recipe](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic) | Own async engine/session lifetimes; migration execution and current application schema are separate concerns |
| [LangSmith data plane](https://docs.langchain.com/langsmith/data-plane) | Managed graph deployment alternative; PostgreSQL persistence and Redis coordination have distinct roles |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | Checkpoints and stores are not an external-action deduplication system |
| [Temporal execution](https://docs.temporal.io/workflow-execution) | Evaluate durable workflow execution if business workflows outgrow simple jobs; avoid competing orchestration ownership |
| [SQS delivery](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/standard-queues.html) and [visibility](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html) | Managed queues still need deduplication, bounded retries and lease/visibility management |
| [Flink concepts](https://nightlies.apache.org/flink/flink-docs-stable/docs/concepts/overview/) | Distinguish stateful continuous stream processing from SSE/token streaming |
| [AWS transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html) | Solve acceptance/notification dual writes; explicitly handle repeated publication |
| [AWS retries and jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/) | Deadline-aware retry ownership; retries can worsen overload |
| [AWS idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/) | Client intent keys, conflict detection and outcome reconciliation |
| [Google SRE overload](https://sre.google/sre-book/handling-overload/) | Bounded admission, criticality and load shedding; do not hide overload in infinite queues |
| [pgvector README](https://github.com/pgvector/pgvector) | Exact-vs-ANN/filter behavior and partitioning tradeoffs; SQL WHERE alone is not filtered ANN recall assurance |
| [Weaviate multi-tenancy](https://docs.weaviate.io/weaviate/manage-collections/multi-tenancy) and [hybrid search](https://docs.weaviate.io/weaviate/search/hybrid) | Dedicated retrieval alternative with tenant-aware operation and BM25F/vector fusion |
| [Pinecone multi-tenancy](https://docs.pinecone.io/guides/data/implement-multitenancy) | Namespace-based tenant boundary; authorization within that boundary remains necessary |
| [Pinecone SDK response models](https://sdk.pinecone.io/python/reference/models.html) | Newer SDKs expose LSN freshness metadata; the locked 9.x adapter does not implement a read-your-writes barrier. Current major docs are not proof of compatibility with an older lock |
| [S3 Versioning](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html) | Persist exact object versions and define retention; storage is a separate transaction boundary |
| [ECS services](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs_services.html) | Scheduler recovery of processes is different from application recovery of work |
| [Anthropic effective agents](https://www.anthropic.com/research/building-effective-agents) | Composable workflows and evaluation-driven complexity; historical 2024 article, not current platform pricing or a hiring rubric |
| [Will Larson: engineering strategy](https://staffeng.com/guides/engineering-strategy/) | Build an engineering narrative from concrete decisions and evidence; not an alleged interview question bank |

The architecture is our synthesis of these sources and this repository's constraints. We have not run a vendor bake-off, measured distributed throughput or verified all managed deployment features with an account. Stages explicitly introduce those experiments before claims.
