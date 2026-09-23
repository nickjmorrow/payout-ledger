"""Structural tests: the conventions in AGENTS.md, checked by a machine.

Each asserts on the shape of the codebase (which module may import what, which
functions take which arguments) where the drift is costly and the check is a
regex or an AST walk. Nothing here duplicates ruff, basedpyright or eslint.

Write each failure message as an argument, not an assertion: the person who
hits it is deciding whether the rule or their code is wrong.

Frontend-only rules live in `frontend/src/structure.test.ts`. These tests need
no database, so the pre-commit hook runs them on every commit.
"""

import ast
import re
from pathlib import Path

from app.worker import handlers

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
APP = BACKEND / "app"
FRONTEND_SRC = REPO / "frontend" / "src"

APP_MODULES = sorted(APP.rglob("*.py"))


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO))


def _imported_roots(path: Path) -> set[str]:
    """Top-level package names this module imports, via AST rather than grep.

    A regex counts a module name inside the docstring explaining why nothing
    may import it, which is how a well-meaning check ends up being deleted.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _reads_the_environment(path: Path) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        # Matches a getenv call or any use of the environ mapping.
        if isinstance(node, ast.Attribute) and node.attr in {"getenv", "environ"}:
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                return True
    return False


def _imported_modules(path: Path) -> set[str]:
    """Fully-qualified module names this file imports, `from` and `import` alike."""
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            # `from app.services import task_service` names a module too.
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


# ------------------------------------------------------------ the boundaries


def test_the_environment_is_read_in_exactly_one_place():
    allowed = APP / "config.py"
    leaked = [
        _relative(path) for path in APP_MODULES if path != allowed and _reads_the_environment(path)
    ]
    assert leaked == [], (
        f"{leaked} read the environment directly. Every environment-dependent value is a field "
        "on Settings in app/config.py — see AGENTS.md > Configuration."
    )


# ------------------------------------------------------------ routes are thin
#
# AGENTS.md > Layout: "Routes exist to translate HTTP into a service call and
# back." The check is narrow on purpose: a route may name a model in a type
# annotation, but the moment it builds a query it has grown a second home for
# business logic that a CLI or the worker cannot reach.


def test_routes_do_not_build_their_own_queries():
    offenders: list[str] = []
    for path in sorted((APP / "api" / "routes").glob("*.py")):
        roots = _imported_roots(path)
        if "sqlalchemy" not in roots:
            continue
        # health.py's `select 1` is a liveness probe, not business logic: it
        # exists precisely to touch the database without going through anything.
        if path.name == "health.py":
            continue
        offenders.append(_relative(path))

    assert offenders == [], (
        f"{offenders} import sqlalchemy. A route validates, authorizes, calls a service and shapes "
        "a response; the query belongs in app/services/ where a second caller can reach it. "
        "See AGENTS.md > Layout."
    )


# ------------------------------------------------------- services take a session
#
# AGENTS.md > Layout: "services/ — Business logic. Every function takes an
# explicit session." Reaching for an ambient request-scoped session is what
# makes service code unusable from the worker.


def _public_async_functions(path: Path) -> list[ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_")
    ]


def test_every_service_function_takes_an_explicit_session():
    offenders: list[str] = []
    for path in sorted((APP / "services").glob("*.py")):
        for function in _public_async_functions(path):
            first = function.args.args[0] if function.args.args else None
            if first is None or first.arg != "session":
                offenders.append(f"{_relative(path)}::{function.name}")

    assert offenders == [], (
        f"{offenders} do not take `session` as their first argument. Every service function takes "
        "an explicit session — see AGENTS.md > Layout."
    )


def test_services_never_import_the_layers_above_them():
    offenders: list[str] = []
    for path in sorted((APP / "services").glob("*.py")):
        upward = {m for m in _imported_modules(path) if m.startswith(("app.api", "app.worker"))}
        if upward:
            offenders.append(f"{_relative(path)} -> {sorted(upward)}")

    assert offenders == [], (
        f"{offenders} import the HTTP layer or the worker. Business logic may only import the "
        "layers below it — models, bus, config. A service that reaches up into "
        "app/api/ is one the worker can no longer call, which is the entire reason services/ "
        "exists. See AGENTS.md > Layout > Which direction imports run."
    )


def test_only_main_imports_the_http_layer():
    """Nothing outside `app/api/` imports `app.api`, except the file that wires it.

    This was briefly a two-symbol allowance: the worker imported `event_frame`
    from `api/schemas.py` and `DEV_USER_ID` from `api/deps.py`, because it needs
    both and they happened to live there. Those moved to `app/wire.py` and
    `app/config.py`, and the rule got to become absolute — which is the whole
    reason the move was worth doing. Do not reintroduce the allowance; move the
    shared thing down instead.
    """
    allowed = APP / "main.py"
    offenders: list[str] = []

    for path in APP_MODULES:
        if path == allowed or path.is_relative_to(APP / "api"):
            continue
        leaked = {m for m in _imported_modules(path) if m.startswith("app.api")}
        if leaked:
            offenders.append(f"{_relative(path)} -> {sorted(leaked)}")

    assert offenders == [], (
        f"{offenders} import app/api/. That package is HTTP, and only main.py wires it. "
        "Whatever is wanted from it belongs lower down: business logic in services/, the shapes "
        "both transports send in app/wire.py, a default in app/config.py. A worker that imports "
        "the HTTP layer is one you cannot run without it. See AGENTS.md > Layout."
    )


# --------------------------------------------------------- the provider seam
#
# AGENTS.md > The provider seam: the rest of the application talks to
# `PaymentProvider` and never to a concrete provider, and `provider_payments`
# is the provider's storage rather than ours. The moment a service reads that
# table directly, reconciliation starts comparing our records to our records
# and passes for the wrong reason.


def test_only_the_provider_package_touches_the_provider_table():
    """`provider_payments` is theirs. We reach it through the Protocol."""
    # models.py is the one exception, and it is not a leak: Alembic diffs every
    # model from one module, so the table has to be *declared* there or no
    # migration would ever create it. Declaring it is not using it.
    allowed = {APP / "models.py"}
    offenders: list[str] = []
    for path in APP_MODULES:
        if path in allowed or path.is_relative_to(APP / "provider"):
            continue
        source = path.read_text()
        # Word-bounded, so that `ProviderPaymentView` does not match. That type
        # is the Protocol's contract — the shape a real provider's JSON would
        # be parsed into — and importing it is exactly what the seam is for. A
        # substring match flagged every correct caller on its first run.
        if re.search(r"\bProviderPayment\b", source) or "provider_payments" in source:
            offenders.append(_relative(path))

    assert offenders == [], (
        f"{offenders} reference the provider's own table. That table stands in for a system we "
        "do not own, and everything outside app/provider/ reaches it through the PaymentProvider "
        "Protocol exactly as it would reach an HTTP API. Reading it directly makes reconciliation "
        "compare our records to our records. See AGENTS.md > The provider seam."
    )


def test_nothing_outside_the_provider_package_imports_the_mock():
    """Depending on the mock is depending on behaviour the real thing lacks.

    `advance_pending` above all: a real provider settles on its own schedule
    and offers no way to make it happen sooner. Code that calls it cannot run
    against a real integration, and a test written against it passes here and
    fails in production.
    """
    allowed = {APP / "provider" / "mock.py", APP / "provider" / "registry.py"}
    offenders: list[str] = []
    for path in APP_MODULES:
        if path in allowed or path.is_relative_to(APP / "provider"):
            continue
        if "provider.mock" in path.read_text():
            offenders.append(_relative(path))

    assert offenders == [], (
        f"{offenders} import the mock provider. Depend on app.provider.base.PaymentProvider and "
        "take the concrete one as an argument, so a fake can be passed in rather than patched "
        "over. See AGENTS.md > The provider seam."
    )


# -------------------------------------------------------- the handler registry
#
# AGENTS.md > The worker: "A task kind is a registration in `HANDLERS`, not a
# branch in `execute`." The settle path is the part that is hard to get right,
# and a kind that reaches it by its own route is a kind that does not retry
# with backoff and does not survive a row deleted underneath it.


def test_task_kinds_are_dispatched_only_through_the_registry():
    source = (APP / "worker" / "handlers.py").read_text()
    body = source[source.index("async def execute(") :]

    assert "task.kind ==" not in body, (
        "execute() branches on task.kind. A task kind is a registration in HANDLERS, not a "
        "branch here — the settle path below the dispatch is the part that must not be "
        "duplicated per kind. See AGENTS.md > The worker."
    )


def test_registering_the_same_kind_twice_is_refused():
    """Two handlers for one kind means whichever imported last silently wins.

    Cheap to check, and the failure it prevents is a task that runs the wrong
    code with no error anywhere.
    """
    kind = next(iter(handlers.HANDLERS), None)
    if kind is None:
        return  # No kinds registered yet; the rule still holds.

    try:
        handlers.register(kind)(lambda session, task: None)  # pyright: ignore[reportArgumentType]
    except ValueError:
        return
    raise AssertionError(
        f"registering {kind!r} a second time was allowed. Two handlers for one kind means "
        "whichever module imported last silently wins. See AGENTS.md > The worker."
    )


# ------------------------------------------------------------- empty packages
#
# AGENTS.md > Layout > One thing per file: "every `__init__.py` is empty.
# Services are imported as modules rather than as loose symbols, so the call
# site says which layer it is calling into."


def test_package_inits_are_empty():
    populated = [
        _relative(path) for path in sorted(APP.rglob("__init__.py")) if path.read_text().strip()
    ]
    assert populated == [], (
        f"{populated} are not empty. A package __init__ that re-exports its modules gives every "
        "symbol two import paths and hides which layer a call goes to; import the module instead "
        "(`from app.services import task_service`). See AGENTS.md > Layout."
    )


# ------------------------------------------------------- the live vocabulary
#
# AGENTS.md > Live updates: the server announces a change under a topic and
# the browser decides what to re-read from it. A topic the browser has not
# heard of is refused by `isChangeEvent` — safely, but silently: the change is
# simply never shown until the next reconnect or backstop poll. Two lists in
# two languages is the drift this catches.


def test_the_browser_knows_every_topic_the_server_announces():
    from typing import get_args

    from app.wire import Topic

    source = (FRONTEND_SRC / "events.ts").read_text()
    match = re.search(r"export const TOPICS = \[([^\]]*)\] as const", source)
    assert match is not None, "frontend/src/events.ts no longer declares `TOPICS` as a const array"
    browser = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    server = set(get_args(Topic))

    assert browser == server, (
        f"the server announces {sorted(server)} and the browser understands {sorted(browser)}. "
        "A topic missing from frontend/src/events.ts is a change the console silently never "
        "shows; one missing from app/wire.py is dead code in the browser. Add it to both, and "
        "decide in `keysToInvalidate` what it re-reads. See AGENTS.md > Live updates."
    )
