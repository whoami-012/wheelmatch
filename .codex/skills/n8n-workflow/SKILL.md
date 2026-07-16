---
name: n8n-workflow
description: Safely discover, design, validate, create, and modify n8n workflows through the connected n8n MCP server. Use for n8n workflow reviews, webhook or schedule automation, node and credential inspection, execution-path debugging, retry and idempotency hardening, and controlled draft updates.
---

# n8n Workflow

Use n8n for asynchronous workflow automation and integrations. Keep authentication, payments, transactional writes, chat delivery, and database consistency in application services.

## 1. Establish scope and authority

- Identify the target environment, project, workflow name, and intended outcome.
- Confirm whether the task is inspect-only, draft modification, safe testing, or production lifecycle management.
- Require explicit approval before publishing, activating, disabling, deleting, or executing a workflow that can cause external side effects.
- Treat draft edits and published production state as different states. Report both.

## 2. Discover before changing

1. Search workflows by the exact or closest verified name. Check recent matches before assuming a workflow is absent.
2. Resolve the workflow ID and preserve it for updates.
3. Read workflow details and inspect:
   - draft and active version IDs;
   - triggers, webhook methods and paths;
   - nodes, versions, credential types, connections, branches and fallback outputs;
   - workflow settings, tags, time zone and execution limits;
   - downstream APIs, sub-workflows and external side effects.
4. List accessible credential metadata only when required. Never request, reveal, log, or copy credential values.
5. Inspect execution history only when needed for diagnosis and redact sensitive payloads from the report.
6. Search for similar workflows before creating a new one. Prefer updating the canonical workflow.

If identity, ownership, target workflow, or production impact remains ambiguous, stop before mutation and request clarification.

## 3. Design the workflow

- Select a trigger by interaction model:
  - Webhook for signed application or provider events.
  - Form for intentional human submission.
  - Schedule for bounded recurring work with an explicit time zone.
  - Chat trigger only for interactive conversational workflows.
- Define a versioned input contract with required fields, types, size limits, event or request ID, timestamps, and trust boundary.
- Define deterministic outputs and backend callback contracts.
- Validate and sanitise input immediately. Reject unsupported versions and connect every fallback branch.
- Add idempotency at the durable system of record. Use stable event, notification, lead, or operation keys.
- Set explicit request and workflow timeouts. Retry only transient failures with bounded backoff and jitter; do not retry validation, authentication, or other permanent failures.
- Send exhausted work to a durable dead-letter path and include correlation identifiers without sensitive payloads.
- Add structured execution metadata, correlation IDs, metrics or audit callbacks. Disable or minimise saved execution data for sensitive flows.
- Keep secrets in n8n credentials or approved environment/variable facilities. Never hardcode them in node parameters.
- Minimise nodes. Prefer native nodes and expressions over Code nodes; use HTTP Request for internal or unsupported integrations only.

## 4. Ground every node

Before writing or changing a workflow:

1. Read the n8n Workflow SDK reference.
2. Retrieve best practices for each relevant workflow technique.
3. Search the connected node catalog for every trigger, integration and utility node.
4. Retrieve exact node type definitions, versions, resources, operations and modes.
5. Resolve load-option or resource-locator values through MCP when required; do not invent IDs.
6. Validate each proposed node configuration before wiring it.
7. Validate the complete workflow or resulting graph and correct every error.

Do not assume that a familiar n8n node or provider integration exists. Distinguish catalog availability from a configured credential.

## 5. Modify safely

- Re-read the workflow immediately before mutation to detect active-version or user changes.
- Make the smallest atomic update that achieves the requested result.
- Update node parameters or settings in place. Do not remove and recreate a node merely to edit it; doing so can disconnect model, memory, tool and other sub-nodes.
- Preserve workflow IDs, webhook IDs and paths, node names used by expressions, credential bindings, sub-workflow references, and external contracts unless the user explicitly approves a breaking change.
- Keep valid branches connected. Ensure IF and Switch fallbacks do not silently drop unexpected items.
- Preserve unrelated nodes and user changes.
- Do not create a duplicate to avoid understanding an existing workflow.
- Leave production triggers unpublished or inactive unless activation is explicitly approved.
- Read the saved workflow back after mutation and compare node count, connections, active state, tags, settings and warnings with the intended result.

## 6. Test without unsafe side effects

- Use synthetic, non-sensitive sample data with explicit schema versions and stable test idempotency keys.
- Prefer node validation, workflow validation, test webhooks, pinned sample data, disabled side-effect nodes, provider sandboxes, and stub endpoints.
- Verify valid, invalid, duplicate, timeout, rate-limit, empty-result and fallback paths.
- Do not execute active workflows or send email, SMS, chat messages, CRM writes, payments, deletions, or production callbacks without explicit approval.
- If required credentials, variables or test endpoints are missing, stop and report the exact requirement instead of inserting fake values.

## 7. Report implementation

Return a concise report containing:

- workflows inspected, including IDs and draft/active status;
- workflows created or changed;
- node-level additions, removals and parameter or connection changes;
- credential types and variables still required, without values;
- node, graph and safe-execution validation performed;
- whether anything was published, activated, disabled or executed;
- remaining risks, blockers and manual actions.

## Examples

Use this skill for requests such as:

- "Inspect the existing lead-routing workflow and add idempotent CRM retries."
- "Create a disabled webhook draft after verifying the required nodes and credentials."
- "Diagnose why the scheduled n8n workflow follows the wrong Switch branch."
- "Review an active workflow's error paths without changing production state."

Do not use this skill for:

- application-only code changes with no n8n workflow impact;
- implementing authentication, payments, chat delivery, migrations, or transactional consistency inside n8n;
- retrieving or exposing credential values;
- one-off external actions that do not require a workflow;
- direct production database access when a scoped application API should own the operation.
