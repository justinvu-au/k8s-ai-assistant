# K8s AI Assistant

A Discord bot that lets you ask natural-language questions about a live Kubernetes cluster and get clear, accurate answers — powered by Claude's tool-use (function calling) and a deliberately read-only Kubernetes integration.

---

## What it does

Mention the bot in Discord with a question like:

- `@K8s Assistant what pods are running in default?`
- `@K8s Assistant describe the plinfra-app pod`
- `@K8s Assistant show me the last 50 lines of logs for pl-stats-agent`
- `@K8s Assistant are there any recent events I should worry about?`

Claude decides which Kubernetes data it needs, calls a small set of pre-defined read-only functions to fetch it, then summarises the result in plain English — instead of you running `kubectl` and parsing raw output yourself.

---

## Architecture

Discord message (@mention)

↓

Discord bot (discord.py, running as a pod inside AKS)

↓

Claude (tool-use / function calling)

↓ calls one or more of:

list_pods · get_pod_logs · describe_pod · list_deployments · get_events · list_namespaces

↓

Kubernetes Python client

↓ (using a scoped ServiceAccount — read-only ClusterRole)

AKS cluster

↓

Plain-English summary ──▶ back to Discord

---

## Why read-only by design

Giving an AI agent the ability to run arbitrary cluster commands — including destructive ones like `delete`, `scale`, or `apply` — is a real production risk: a hallucinated or misinterpreted instruction could take down a live service. This project deliberately avoids that by enforcing read-only access at **two independent layers**:

1. **Application layer** — Claude is only given tool definitions for informational functions (`list_pods`, `describe_pod`, `get_pod_logs`, etc.). There is no `delete_pod` or `scale_deployment` function in existence for it to call, hallucinated or otherwise.
2. **Kubernetes RBAC layer** — the bot runs under a dedicated `ServiceAccount` bound to a `ClusterRole` that only grants `get` and `list` verbs. Even if the application code had a bug, the Kubernetes API itself would reject any mutating request from this identity.

This is defence in depth — two independent reasons mutation can never happen, rather than relying on a single point of trust in either the prompt or the code.

A natural v2 extension would be a human-in-the-loop approval flow (e.g. a Discord reaction to confirm) before allowing scoped, audited mutating actions like `kubectl rollout restart`.

---

## Tech stack

| Layer | Technology |
|---|---|
| Bot framework | discord.py |
| AI agent | Anthropic Claude (tool use / function calling) |
| Kubernetes access | Official Python Kubernetes client |
| RBAC | Dedicated ServiceAccount + read-only ClusterRole |
| Deployment | Containerised, running as a pod inside AKS |
| Container registry | Azure Container Registry |

---

## Project structure

```

k8s-ai-assistant/

├── app/

│   ├── main.py            # Discord bot entrypoint

│   ├── k8s_tools.py       # Read-only Kubernetes client functions

│   ├── claude_agent.py    # Claude tool-use orchestration

│   ├── requirements.txt

│   └── Dockerfile

├── k8s/

│   ├── rbac.yaml           # ServiceAccount, ClusterRole, ClusterRoleBinding

│   └── deployment.yaml

├── .env.example

└── .gitignore

```
---

## Available tools (what Claude can call)

| Function | Purpose |
|---|---|
| `list_pods` | Pod status, restart count, node, age |
| `get_pod_logs` | Last N lines of a pod's logs |
| `describe_pod` | Container states, readiness, recent events for one pod |
| `list_deployments` | Replica status (ready vs desired) |
| `get_events` | Recent cluster events (scheduling failures, image pull errors, etc.) |
| `list_namespaces` | Which namespaces the bot is permitted to read |

All functions are scoped to an explicit namespace allow-list (`default`, `monitoring`, `kube-system`) defined in code, independent of the RBAC layer.

---

## Local development

```bash
cd app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp ../.env.example ../.env
# Fill in DISCORD_BOT_TOKEN and ANTHROPIC_API_KEY

python main.py
```

Locally, the bot falls back to your kubeconfig (`~/.kube/config`) rather than in-cluster config — useful for development, but the deployed version always runs under the scoped ServiceAccount.

---

## Deploying to AKS

```bash
# 1. Apply RBAC (ServiceAccount + read-only ClusterRole)
kubectl apply -f k8s/rbac.yaml

# 2. Create the secrets
kubectl create secret generic k8s-ai-assistant-secrets \
  --from-literal=discord-bot-token="YOUR_TOKEN" \
  --from-literal=anthropic-api-key="YOUR_KEY"

# 3. Build and push the image
az acr login --name plstatsacr
docker build --platform linux/amd64 -t plstatsacr.azurecr.io/k8s-ai-assistant:v1 ./app
docker push plstatsacr.azurecr.io/k8s-ai-assistant:v1

# 4. Deploy
kubectl apply -f k8s/deployment.yaml
kubectl get pods -l app=k8s-ai-assistant
kubectl logs -l app=k8s-ai-assistant --tail=20
```

---

## Operational notes

**Redeploying after a code change:**
```bash
docker build --platform linux/amd64 -t plstatsacr.azurecr.io/k8s-ai-assistant:v1 ./app
docker push plstatsacr.azurecr.io/k8s-ai-assistant:v1
kubectl rollout restart deployment k8s-ai-assistant
```

**Checking logs:**
```bash
kubectl logs -l app=k8s-ai-assistant --tail=50 --follow
```

**Rotating secrets:**
```bash
kubectl delete secret k8s-ai-assistant-secrets
kubectl create secret generic k8s-ai-assistant-secrets \
  --from-literal=discord-bot-token="NEW_TOKEN" \
  --from-literal=anthropic-api-key="NEW_KEY"
kubectl rollout restart deployment k8s-ai-assistant
```

**Important — always verify your kubectl context before deploying:**
```bash
kubectl config current-context
```
This bot, like every project in this portfolio, shares Azure infrastructure across multiple repos. Deploying to the wrong cluster context produces confusing failures (image pull 401s, missing secrets) that look like permission or build issues but are actually just pointed at the wrong cluster. Always confirm the context first.

---

## Lessons learned

The most time-consuming bug during development wasn't in the application code at all — it was a `kubectl` context mismatch. The RBAC, secrets, and deployment had all been correctly created, but against a *different* AKS cluster than the one whose Container Registry permissions had been verified. The error surfaced as a generic `401 Unauthorized` image pull failure, which looked identical to a missing ACR role assignment — a real reminder that in multi-cluster environments, confirming `kubectl config current-context` should be the very first troubleshooting step, before checking application-level configuration.

