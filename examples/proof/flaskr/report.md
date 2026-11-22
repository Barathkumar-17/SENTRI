<!-- sentri-report -->
## Sentri readiness: 100

`score = 100 x (5 + 0.5 x 0) / 5 = 100`

### Grid
| Flow | empty_input | double_submit | expired_session | slow_network | rapid_repeat |
|---|---|---|---|---|---|
| create_post | ✅ | ✅ | ✅ | ✅ | ✅ |

### Agentic exploration
Recon: 4 pages, 3 endpoints, 1 login-only (/create).
Strategy: simple Flaskr blog with user registration, login and post creation, 8 hypotheses.

#### Attack tree
- **unauth_create_allowed** - Unauthenticated visitors can create a post via POST /create
  - 1. `[anon] request(method='POST', path='/create', content_type='application/x-www-form-urlencoded')`
    - _why: Attempt to create a post without authentication to test unauthenticated POST /create access._
    - 2. `[anon] refresh`
      - _why: Refresh the home page to see if the unauthenticated POST created a visible post entry._
      - 3. `[anon] request(method='POST', path='/create', content_type='application/x-www-form-urlencoded')`
        - _why: Send a proper POST with title and body as anon to see if creation succeeds_
        - 4. `[anon] request(method='POST', path='create', content_type='application/x-www-form-urlencoded')`
          - _why: Send a proper POST with title and body as anon to verify unauthenticated creation_
          - 5. `[anon] request(method='POST', path='/create', content_type='application/x-www-form-urlencoded')`
            - _why: Send a POST /create with title and body fields as anon to test if post creation succeeds without authentication._
            - 6. `[anon] request(method='POST', path='/create', content_type='application/x-www-form-urlencoded')`
              - _why: Send a POST /create as anon with a unique title containing the XSS marker to later check if it appears on the home page, testing unauthenticated creation._
              - 7. `[anon] request(method='POST', path='/create', content_type='application/x-www-form-urlencoded')`
                - _why: Send a POST /create with title and body fields as anon to verify if unauthenticated creation succeeds_
                - 8. `[anon] request(method='POST', path='/create', content_type='application/x-www-form-urlencoded')`
                  - _why: Attempt to create a post as anon with required form fields to see if creation succeeds._
                  - 9. `[anon] request(method='POST', path='/create', content_type='application/x-www-form-urlencoded')`
                    - _why: Send a POST to /create with explicit title/body fields as anon to see if a post is created and visible on the home page._
                    - 10. `[anon] navigate(path='/create')`
                      - _why: Check if anonymous user can access the post creation page; if it redirects to login, creation likely not allowed despite 302 responses._
                      - 11. `[anon] navigate(path='/')`
                        - _why: Check if unauthenticated POSTs actually created visible posts by visiting the home page._
                        - 12. `[anon] request(method='POST', path='/create', content_type='application/x-www-form-urlencoded')`
                          - _why: Send an unauthenticated POST to /create with title and body fields to see if a post is created, then we can verify on the home page._
                          - 13. `[anon] request(method='POST', path='create', content_type='application/x-www-form-urlencoded')`
                            - _why: Send a POST /create with a unique title to test if unauthenticated creation persists and can be seen on the home page._
- **xss_in_post_title** - Post titles are rendered without sanitisation, enabling stored XSS
  - 14. `[anon] switch_user(user='a')`
    - _why: Switch to user a to be able to create a post for XSS test._
    - 15. `[a] request(method='POST', path='create', content_type='application/x-www-form-urlencoded')`
      - _why: Create a post as user a with the XSS marker in the title via a direct POST request to /create, testing for stored XSS._


Flaky: 0 · N/A: 0 · Errors: 0 · Reused: 0 · Probes: 0 (0 fail) · LLM: llm, $0.00 · Duration: 10m22s
