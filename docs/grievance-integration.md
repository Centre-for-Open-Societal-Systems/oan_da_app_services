# Grievance integration

DA Services does not store grievance cases. The caller authenticates with DA Services. DA Services forwards that same FE `Authorization` bearer to the Grievance Service. There is no separate service-account login.

The live HTTP client always runs. Configure **Grievance Base URL** on DA Services Settings. The call table and field mapping are in [grievance-da-http.md](grievance-da-http.md). This page is the path those calls take.

Officer actions (resolve, reopen, close, reassign) are not on the DA routes.

## Flow

```mermaid
sequenceDiagram
  actor Agent
  participant DA as DA Services
  participant Client as Grievance client
  participant GS as Grievance Service

  Agent->>DA: /api/v1/da-services/grievances, FE bearer
  Note over DA: No DA role/scope gate; Frappe login only
  Note over Client,GS: Forward FE Authorization bearer to gateway /v1/...
  alt Submit
    Client->>GS: POST /v1/grievances
  else Read one ticket
    Client->>GS: GET /v1/grievances/{ticket}/timeline
  else List or summary
    Client->>GS: GET /v1/grievances or /summary
  else Form options
    Client->>GS: GET /v1/submitters/options, no bearer
    Client->>GS: GET /v1/grievances/options
  end
  DA-->>Agent: DA response
```

A failed live submit returns HTTP 503. Offline queue / `POST /grievances/sync/retry` is not finalized yet. A repeated `client_submission_uuid` returns the original ticket.
