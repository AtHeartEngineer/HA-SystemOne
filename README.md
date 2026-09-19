[![Tests](https://github.com/AtHeartEngineer/HA-SystemOne/actions/workflows/tests.yaml/badge.svg)](https://github.com/AtHeartEngineer/HA-SystemOne/actions/workflows/tests.yaml)
[![hassfest](https://github.com/AtHeartEngineer/HA-SystemOne/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/AtHeartEngineer/HA-SystemOne/actions/workflows/hassfest.yaml)
[![HACS](https://github.com/AtHeartEngineer/HA-SystemOne/actions/workflows/hacs.yaml/badge.svg)](https://github.com/AtHeartEngineer/HA-SystemOne/actions/workflows/hacs.yaml)
[![Release](https://img.shields.io/github/v/release/AtHeartEngineer/HA-SystemOne)](https://github.com/AtHeartEngineer/HA-SystemOne/releases)

# SystemOne for Home Assistant

SystemOne is a Home Assistant integration for decision-model APIs that implement
the TypeSafe Jev `POST /v1/systemone` format.

It works with:

- hosted [TypeSafe Jev](https://typesafe.ai);
- self-hosted SystemOne-compatible servers;
- custom model hosts, including R9V/Qwen proxies;
- authenticated and unauthenticated endpoints.

The integration sends a shared state plus one or more typed questions and receives
probabilities, choices, or scores. It does not load a model inside Home Assistant.

## Features

- Native Home Assistant `AI Task` entity for structured decisions.
- Boolean fields mapped to SystemOne `noul` questions.
- Select/enum fields mapped to `choice` questions.
- Bounded numeric fields mapped to ordinal `score` questions.
- Multiple AI Task fields evaluated in one `/v1/systemone` request.
- Configurable API base URL, optional bearer token, and model ID.
- Existing `jev.noul`, `jev.choice`, `jev.score`, and `jev.ask` actions.
- YAML-defined decision sensors and binary sensors.
- Optional Assist conversation router.
- Usage, latency, estimated-cost, and daily-budget entities.

## Compatibility note

This project was originally HA-Jev. The Home Assistant domain remains `jev` so
existing entity IDs, service calls, YAML configuration, and automations continue to
work. The integration name and provider behavior are now generic SystemOne.

## Installation

Home Assistant 2026.9 or newer is required.

### HACS

Add this fork as a custom repository:

1. Open HACS.
2. Open the three-dot menu and choose **Custom repositories**.
3. Enter `https://github.com/AtHeartEngineer/HA-SystemOne`.
4. Select **Integration** and choose **Add**.
5. Search for **SystemOne**, install it, and restart Home Assistant.

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=AtHeartEngineer&repository=HA-SystemOne&category=integration)

### Manual

Download the [latest release](https://github.com/AtHeartEngineer/HA-SystemOne/releases/latest),
copy `custom_components/jev` into `config/custom_components/`, and restart Home
Assistant.

## Configuration

Go to **Settings → Devices & services → Add integration → SystemOne**.

[![Add SystemOne to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=jev)

| Setting | Default | Description |
|---|---|---|
| Base URL | `https://api.typesafe.ai` | Root URL for the compatible API |
| API token | blank | Optional bearer token |
| Model | `jev-latest` | Model identifier sent to the server |

The integration normalizes base URLs ending in `/`, `/v1`, or `/v1/systemone` and
always sends requests to the correct `/v1/systemone` endpoint. When the token is
blank, no `Authorization` header is sent.

Setup tries the optional `GET /v1/models` route without running inference. A server
without that route is still supported; the first real request validates the
SystemOne endpoint.

### TypeSafe Jev

```text
Base URL:  https://api.typesafe.ai
API token: <your TypeSafe token>
Model:     jev-latest
```

### Self-hosted server

```text
Base URL:  http://192.168.1.50:8000
API token: <leave blank if authentication is disabled>
Model:     Qwen/Qwen3.8-Flash-Next
```

The model can be any identifier accepted by the configured server. There is no
separate self-hosted mode.

## Native AI Task entity

Each configured server creates `ai_task.jev_ai_task`. The legacy `jev` domain keeps
the integration compatible with existing installations; the entity ID can be
renamed normally.

Supported output fields are translated as follows:

| Home Assistant structure | SystemOne question | Result |
|---|---|---|
| Boolean | `noul` | Python boolean using a `0.5` threshold |
| Select/enum | `choice` | Exact configured option value |
| Bounded number | `score` | Value mapped back into the requested range |
| Free text | unsupported | Clear error before any API request |

One structured task becomes one HTTP request. For example, the following three
fields become three questions under one shared state:

```yaml
action: ai_task.generate_data
data:
  entity_id: ai_task.jev_ai_task
  task_name: office_state
  instructions: >-
    Office presence sensor: on.
    Desk computer power: 138 watts.
    Ceiling lights: on.
    Last motion: 12 seconds ago.
  structure:
    occupied:
      description: Is someone currently in the office?
      required: true
      selector:
        boolean:

    activity:
      description: What is the most likely state of the office?
      required: true
      selector:
        select:
          options:
            - empty
            - working
            - relaxing
            - uncertain

    confidence_needed:
      description: How strongly should an automation rely on this interpretation?
      required: true
      selector:
        number:
          min: 0
          max: 5
          step: 1
response_variable: result
```

The result is ordinary Home Assistant structured data:

```yaml
result.data.occupied
result.data.activity
result.data.confidence_needed
```

Example value:

```json
{
  "occupied": true,
  "activity": "working",
  "confidence_needed": 4
}
```

SystemOne probability and confidence metadata is kept out of the requested AI Task
schema. Request counts, latency, and token usage are available in diagnostics.

## Typed actions

The original typed actions remain available for automations that want direct access
to SystemOne response metadata.

| Action | Question type | Main result |
|---|---|---|
| `jev.noul` | Yes/no probability | `noul`, plus thresholded `is_true` |
| `jev.choice` | One option from 2–255 choices | `choice`, probabilities, confidence |
| `jev.score` | Ordered scale of 2–10 levels | weighted score, legend, confidence |
| `jev.ask` | Several mixed questions | typed answers from one request |

```yaml
action: jev.noul
data:
  state: >-
    Washing-machine power: {{ states('sensor.washing_machine_power') }} W
  instructions: Is the washing cycle finished?
  threshold: 0.7
response_variable: laundry
```

Related questions should use `jev.ask` so they share one request and one state.

## YAML decision sensors

Existing YAML configuration remains supported:

```yaml
jev:
  - name: Laundry
    scan_interval: 300
    entities:
      - sensor.washing_machine_power
      - binary_sensor.laundry_door
    questions:
      - name: Laundry forgotten
        type: noul
        instructions: Is the laundry finished but still in the machine?
        threshold: 0.7
      - name: Reminder urgency
        type: score
        instructions: How urgently should someone be reminded?
        criteria:
          - Not at all
          - When convenient
          - Right now
```

Every context batches its questions into one SystemOne request. See the
[`examples/`](examples/) directory for more automation patterns.

## Assist conversation router

The optional conversation entity routes supported smart-home commands through the
configured SystemOne model and Home Assistant's intent system. It supports on, off,
toggle, brightness, and state questions. Unsupported or low-confidence requests can
be forwarded to a configured fallback conversation agent.

Only entities exposed to Assist are included. Whole-home commands are refused by
default except for turning everything off.

## Options and diagnostics

Integration options include:

- daily input-token budget;
- price per million input tokens for estimated-cost sensors;
- fallback conversation agent;
- minimum conversation confidence;
- whole-home command permission.

Diagnostics include the endpoint URL, configured model, authentication presence,
usage totals, and the last AI Task request statistics. API tokens and Authorization
headers are always redacted. AI Task instructions and household state are not added
to AI Task diagnostics.

## Limitations

- AI Task supports decision-oriented structured data only.
- Free-form text, images, attachments, and AI Task tool calling are not supported.
- A task containing any unsupported field is rejected before inference.
- Large or continuous numeric ranges use up to ten representative score levels and
  are mapped back into the requested range.
- Optional AI Task fields are currently answered rather than omitted.
- Probability and confidence calibration depends on the configured model host.
- Do not use model decisions as the sole control for safety-critical equipment.

## Troubleshooting

| Problem | Check |
|---|---|
| Cannot connect | Base URL, DNS, port, and server availability |
| Authentication rejected | Token value and server authentication settings |
| TLS failure | Certificate validity and hostname |
| Incompatible response | Server implements the TypeSafe Jev `/v1/systemone` response format |
| Unknown choice | Server returned a value outside the requested select options |
| Unsupported AI Task field | Use boolean, select/enum, or bounded number selectors |

Debug logging can be enabled with:

```yaml
logger:
  logs:
    custom_components.jev: debug
```

Never publish logs or diagnostics without checking them for household information.

## Development

```bash
pip install -r requirements-test.txt
pytest
ruff check custom_components tests
ruff format --check custom_components tests
mypy --strict --ignore-missing-imports custom_components/jev
```

The project is validated with pytest, Ruff, strict mypy, hassfest, and the HACS
validation action.

## Project links

- [Repository](https://github.com/AtHeartEngineer/HA-SystemOne)
- [Releases](https://github.com/AtHeartEngineer/HA-SystemOne/releases)
- [Issues](https://github.com/AtHeartEngineer/HA-SystemOne/issues)
