import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config

from incidentops.durable.config import WorkflowSettings
from incidentops.durable.runtime import create_runtime
from incidentops.ingestion import load_documents
from incidentops.models import Principal, Question


def upgrade_database(settings):
    configuration = Config("alembic.ini")
    configuration.set_main_option(
        "sqlalchemy.url", settings.workflow_database_url.replace("%", "%%")
    )
    command.upgrade(configuration, "head")


async def enqueue_fixtures(runtime, *, execute=False):
    completed = []
    today = datetime.now(UTC).date()
    for document in load_documents(runtime.settings.corpus_dir):
        if (
            document.status != "active"
            or document.access_group != "operators"
            or document.effective_from > today
            or (document.effective_to and document.effective_to <= today)
        ):
            continue
        actor = Principal(document.tenant_id, f"{document.tenant_id}-demo-operator", ("operators",))
        job = await runtime.submit_document(
            actor, f"fixture:{document.document_id}:v{document.version}", document
        )
        if execute:
            await runtime.run_job(job["id"])
            view = await runtime.job_view(actor, job["id"])
            if view["status"] != "succeeded":
                raise RuntimeError("Fixture ingestion did not succeed; inspect its durable job")
        completed.append(job["id"])
    return completed


async def demo(settings):
    runtime = await create_runtime(settings)
    try:
        accepted = await enqueue_fixtures(runtime, execute=True)
        actor = Principal("acme", "acme-demo-operator", ("operators",))
        job = await runtime.submit_question(
            actor, "demo-answer", Question(question="What does PUB-403 mean in release 2.7?")
        )
        await runtime.run_job(job["id"])
        view = await runtime.job_view(actor, job["id"])
        return {
            "provider": "fake",
            "quality_evidence": False,
            "ingestion_jobs_succeeded": len(accepted),
            "job_id": job["id"],
            "answer_status": view["result"]["assessment"]["status"],
            "assessment": view["result"],
            "duplicate_worker_ran": await runtime.run_job(job["id"]),
        }
    finally:
        await runtime.aclose()


async def action(args, settings):
    runtime = await create_runtime(settings)
    try:
        if args.command == "index-init":
            initialize = getattr(runtime.index, "initialize", None)
            if initialize:
                await initialize()
            return {"vector_backend": settings.vector_backend, "initialized": True}
        if args.command == "enqueue-fixtures":
            return {"accepted_job_ids": await enqueue_fixtures(runtime)}
        if args.command == "seed-local":
            return {"executed_job_ids": await enqueue_fixtures(runtime, execute=True)}
        if args.command == "evaluate":
            from incidentops.durable.evaluation import evaluate_workflows

            report = await evaluate_workflows(runtime, args.dataset)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, indent=2) + "\n")
            return report
        if args.command == "run":
            return {"executed": await runtime.run_job(args.job_id)}
        if args.command == "dispatch":
            from incidentops.durable.broker import CeleryPublisher, create_celery_app, dispatch_once

            publisher = CeleryPublisher(create_celery_app(settings))
            try:
                while True:
                    await runtime.repo.recover_expired(settings.worker_max_attempts)
                    count = await dispatch_once(runtime.repo, publisher)
                    if args.once:
                        return {"dispatched": count}
                    await asyncio.sleep(1)
            finally:
                await publisher.aclose()
        raise ValueError("Unknown command")
    finally:
        await runtime.aclose()


def main():
    parser = argparse.ArgumentParser(description="IncidentOps v2 durable async workflows")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ["demo", "migrate", "index-init", "enqueue-fixtures", "seed-local"]:
        commands.add_parser(name)
    run = commands.add_parser("run", help="Execute one persisted job locally (no broker)")
    run.add_argument("job_id")
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("--dataset", type=Path, default=Path("data/eval/dev.jsonl"))
    evaluation.add_argument("--output", type=Path)
    dispatch = commands.add_parser(
        "dispatch", help="Publish durable outbox events and recover expired leases"
    )
    dispatch.add_argument("--once", action="store_true")
    for name, port in [("serve", 8000), ("serve-retrieval", 8001)]:
        serve = commands.add_parser(name)
        serve.add_argument("--host", default="127.0.0.1")
        serve.add_argument("--port", default=port, type=int)
    args = parser.parse_args()
    try:
        if args.command == "demo":
            with TemporaryDirectory(prefix="incidentops-v2-") as directory:
                settings = WorkflowSettings(
                    _env_file=None,
                    provider="fake",
                    tracing=False,
                    vector_backend="local",
                    object_backend="local",
                    object_root=Path(directory) / "objects",
                    workflow_database_url=f"sqlite+aiosqlite:///{directory}/jobs.db",
                    corpus_dir=Path("data/corpus"),
                )
                upgrade_database(settings)
                result = asyncio.run(demo(settings))
        else:
            settings = WorkflowSettings()
            if args.command == "migrate":
                upgrade_database(settings)
                result = {"migrated": True}
            elif args.command.startswith("serve"):
                import uvicorn

                from incidentops.durable.api import create_workflow_app

                uvicorn.run(
                    create_workflow_app(settings, retrieval_only=args.command == "serve-retrieval"),
                    host=args.host,
                    port=args.port,
                    log_level="warning",
                    limit_concurrency=settings.http_concurrency,
                )
                return
            else:
                result = asyncio.run(action(args, settings))
        print(json.dumps(result, indent=2))
        if args.command == "evaluate" and not result["all_passed"]:
            raise SystemExit(1)
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(
            f"Workflow command failed ({type(exc).__name__}); "
            "check configuration and v2 setup guide.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
