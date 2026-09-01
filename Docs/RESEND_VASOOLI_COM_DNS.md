# Resend DNS handoff for `vasooli.com`

Resend domain ID: `5001db5f-9346-4b9b-9c97-2ce0c027872e`

Current status: `not_started`

Current public nameservers:

- `ns1.sedoparking.com`
- `ns2.sedoparking.com`

Publish these records with the DNS provider that controls `vasooli.com`:

| Purpose | Type | Host/name | Value | Priority |
|---|---|---|---|---|
| DKIM | TXT | `resend._domainkey` | `p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDVPdlL9vMXvc+z5LImhGK+mQl9ZmKkaLv4d1cyJ/jvIO5lqttw6D/OIBKf52bD0ALM+WWeJOxKE6aErwRO29lauVSZ/2iekL+EHQdQNMLSm5YF5DzV5gEO0EjP+x5pCtSWunohXXFAH+JlHO+e82wwFLiM20Sc4o59ol4gd53ekwIDAQAB` | — |
| SPF | MX | `send` | `feedback-smtp.us-east-1.amazonses.com` | `10` |
| SPF | TXT | `send` | `v=spf1 include:amazonses.com ~all` | — |

After DNS propagation, open the Resend Domains page and trigger verification for
`vasooli.com`. OTP and password-recovery delivery from
`Vasooli <noreply@vasooli.com>` will remain blocked until the domain is verified.
