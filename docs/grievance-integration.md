# Grievance integration

DA Services does not store grievance cases. The caller authenticates with DA Services. DA Services then calls the Grievance Service with a separate bearer. That token is not the DA token and not a Crop Sown staff token.

`grievance_backend` defaults to `mock`. Set it to `http` for the live service. The call table, login steps, and field mapping are in [grievance-da-http.md](grievance-da-http.md). This page is the path those calls take.

Officer actions (resolve, reopen, close, reassign) are not on the DA routes.

## Flow

```mermaid
sequenceDiagram
  actor Agent
  participant DA as DA Services
  participant Client as Grievance client
  participant GS as Grievance Service

  Agent->>DA: /api/v1/da-services/grievances, DA bearer
  DA->>DA: Role and region or woreda scope
  alt grievance_backend is mock
    DA->>Client: In-memory fixture
  else http
    Client->>GS: Agent login, once, then cached bearer
    alt Submit
      Client->>GS: POST /v1/grievances
    else Read one ticket
      Client->>GS: GET /v1/grievances/{ticket}
      Client->>GS: GET timeline when the ticket GET is missing
    else List or summary
      Client->>GS: GET /v1/grievances or /summary
    else Form options
      Client->>GS: GET /v1/submitters/options, no bearer
    end
  end
  DA-->>Agent: DA response
```

A failed live submit can be queued as a DA Integration Event. `POST /grievances/sync/retry` retries that event. DA Services Admin only. A repeated `client_submission_uuid` returns the original ticket.
