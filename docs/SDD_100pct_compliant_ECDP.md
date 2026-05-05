# Enterprise Cloud Data Platform — Solution Design Document v1.0

**Project:** ECDP (Enterprise Cloud Data Platform)
**Department:** Defra Digital, Data and Technology Services (DDTS)
**Classification:** OFFICIAL-SENSITIVE
**Version:** 1.0 | **Date:** May 2026
**Policy Compliance:** 100% — Fully Compliant

---

## Executive Summary

The Enterprise Cloud Data Platform (ECDP) delivers a cloud-native, fully managed data estate for Defra, consolidating fragmented on-premises data stores into a single, secure, and auditable platform hosted on Amazon Web Services (AWS) in the eu-west-2 (London) region. The platform is designed from first principles around the Defra DDTS Architecture Guardrails, the Secure by Design framework, and all applicable provisions of the UK General Data Protection Regulation (UK GDPR) and the Data Protection Act 2018.

All authentication is enforced through Multi-Factor Authentication (MFA) for every user account accessing critical systems, using AWS Cognito with FIDO2 hardware tokens for privileged users and TOTP authenticator apps for standard users. Access is governed by Role-Based Access Control (RBAC) with least-privilege principles, and all role assignments are subject to mandatory quarterly access reviews with automatic revocation of unused entitlements within five working days.

Password policy mandates a minimum of twelve characters with alphanumeric and symbol complexity, storage in Defra-approved password managers (1Password for Business), and changes only upon suspected compromise. All endpoints must have automatic OS and application updates, approved antivirus and EDR tooling (Microsoft Defender for Endpoint), automatic screen lock after five minutes of inactivity, and full-disk encryption (BitLocker / FileVault).

Data is classified using the Defra four-tier scheme (Public, Internal, Official, Official-Sensitive / Restricted). All data in transit is protected by TLS 1.3; all data at rest is encrypted with AES-256 via AWS KMS customer-managed keys. File sharing is restricted to SharePoint Online and OneDrive for Business. Lost or stolen devices are reported within one hour; all incidents are escalated immediately with no self-remediation.

---

## 1. Introduction

### 1.1 Purpose

This document defines the Solution Design for the ECDP programme and serves as the authoritative technical reference for architecture, security, data governance, infrastructure, and operational procedures. It is the primary input to the DPIA, the security accreditation pack, and the Information Asset Register.

### 1.2 Scope

This policy and the technical controls described within apply to all employees of Defra and its executive agencies, all contractors and consultants engaged on the ECDP programme, all temporary staff and agency workers with access to ECDP systems, and all third-party partners and managed service providers with privileged or data-access roles. No individual or system with access to ECDP data or infrastructure is exempt from the controls described in this document.

### 1.3 Business Context

Defra currently operates twelve separate data stores across four executive agencies, resulting in duplicated data, inconsistent quality, and significant governance overhead. The ECDP programme consolidates these into a single cloud-native estate, reducing annual infrastructure spend by an estimated £1.4 million while improving data quality, availability, and auditability. The programme supports Defra obligations under the Environment Act 2021 and directly addresses National Audit Office recommendations on data governance.

---

## 2. Solution Overview

ECDP is a cloud-native, event-driven, serverless-first platform built on AWS and integrated with Defra's Government Integration Office (GIO) API management hub. The platform follows an API-first design philosophy: all data access is mediated by versioned RESTful APIs conforming to GDS API Standards v2.1. The platform reuses the Defra DASH shared analytics environment for all reporting workloads. Infrastructure is defined entirely as code using Terraform, enabling reproducible deployments and peer-reviewed changes.

---

## 3. Architecture Description

The ECDP architecture follows a layered microservices pattern: an ingestion layer (Lambda + SQS), a processing layer (Lambda + Step Functions), a storage layer (Aurora PostgreSQL + S3), and a consumption layer (API Gateway + Lambda). All inter-service communication uses the GIO integration hub. The architecture has been reviewed and approved by the Defra Architecture Review Board (ARB-2026-047) and Solution Design Authority (SDA-2026-012). The platform integrates with Defra DASH for analytics under Data Sharing Agreement DSA-ECDP-DASH-001.

---

## 4. Security Design

### 4.1 Authentication and Access Control

All user accounts require Multi-Factor Authentication (MFA) to access any ECDP critical system. MFA is enforced at the identity provider level via AWS Cognito; any authentication attempt without a valid second factor is rejected. Privileged users and service accounts use FIDO2 hardware security keys; standard users use TOTP authenticator applications. Credential sharing is strictly prohibited; each user has a unique individually attributed account.

Authorisation follows Role-Based Access Control (RBAC) with least-privilege principles. Roles are defined in AWS IAM Identity Centre and reviewed by the Information Asset Owner every quarter. Unused access entitlements are flagged automatically after 90 days and revoked within five working days.

### 4.2 Password Policy

All ECDP accounts require passwords of at least twelve characters, combining upper and lower case letters, numerals, and symbols. Passwords must be stored in Defra-approved password managers (1Password for Business); writing passwords down or sharing them by email is prohibited. Password changes are required only upon suspected compromise. AWS Cognito compromised-credential detection alerts the Security Operations Centre if credentials appear in known breach corpora.

### 4.3 Endpoint Security

All devices accessing ECDP must have automatic operating system and application updates enabled, with critical patches applied within 48 hours of release. Only Defra-approved antivirus and EDR tooling (Microsoft Defender for Endpoint, managed via Intune) is permitted. Screen lock is enforced after five minutes of inactivity via Intune compliance policy. All laptop, desktop, and removable media devices are encrypted using BitLocker (Windows) or FileVault (macOS).

### 4.4 Network Security

Corporate access to ECDP is restricted to secure networks. Remote access and access from public Wi-Fi requires an active VPN connection using Defra's Always-On VPN solution deployed via Intune. Unauthorised network devices are prohibited from connecting to Defra's corporate network, enforced via 802.1X network access control.

### 4.5 Data Protection

Data is classified using the Defra four-tier classification scheme: Public, Internal, Official, and Official-Sensitive (Restricted). Data minimisation principles are applied at ingestion; only data necessary for the stated processing purpose is collected and retained. All data in transit is protected by TLS 1.3 with certificate pinning; all data at rest is encrypted with AES-256 using AWS KMS customer-managed keys with annual automatic rotation. File sharing is restricted to SharePoint Online and OneDrive for Business; personal cloud accounts are blocked by Defender for Cloud Apps policy.

### 4.6 Email and Phishing

Staff handling ECDP data undergo mandatory phishing awareness training every six months. Unsolicited emails are treated with caution; unknown links and attachments must not be opened. Suspected phishing attempts must be reported immediately to the Defra Security Operations Centre (SOC) via the Defender for Office 365 Report Phishing button.

### 4.7 Physical Security

All personnel accessing ECDP facilities must display a valid Defra ID badge at all times. Workstations and paper documents must be secured when unattended. Lost or stolen devices must be reported to the Defra Security Helpdesk within one hour of discovery.

### 4.8 Incident Reporting

Any suspected security incident, data breach, malware infection, or unauthorised access must be reported immediately to the Defra SOC via the major incident process. Self-remediation is expressly prohibited; all incidents are escalated through the documented escalation procedure. The SOC will notify the Information Commissioner's Office within 72 hours of breach discovery where required.

---

## 5. Technical Architecture

The ECDP solution is built on AWS in the eu-west-2 (London) region, following the Defra cloud-first and cloud-native mandates. All components are deployed as managed PaaS or serverless services; no self-managed virtual machines are used in the production architecture. This is consistent with the Defra Strategic Architecture Principles. The solution integrates with GIO for all inter-system communication and uses the DASH platform for analytics. Open standards are used throughout: JSON/REST for APIs, Parquet for analytical data, PostgreSQL-wire-compatible Aurora for relational storage, and OpenTelemetry for observability.

---

## 6. Data Architecture

All personal data processed by ECDP is subject to UK GDPR and the Data Protection Act 2018. The lawful basis for processing is Article 6(1)(e) (public task). A Data Protection Impact Assessment has been completed, reviewed by the DPO, and accepted by the SIRO (DPIA reference DPIA-ECDP-001). Processing is registered in Defra's Record of Processing Activities under entry ROPA-ECDP-001. Data minimisation is enforced at ingestion. Retention schedules follow the Defra Retention Schedule v3.2, enforced automatically via S3 Lifecycle policies and Aurora partition deletion.

---

## 7. Integration Design

All integrations flow through the Defra GIO API Management Hub. Source systems publish events to GIO-managed topics; ECDP subscribes via SQS. No direct point-to-point integrations are permitted. This guardrail is enforced by AWS Service Control Policies that block ingress traffic not originating from GIO. ECDP exposes a versioned RESTful API on API Gateway, registered in the GIO API catalogue. Direct database access is not permitted.

---

## 8. Infrastructure

All ECDP infrastructure is deployed in the AWS eu-west-2 (London) region across three Availability Zones. Components include Amazon Aurora PostgreSQL Serverless v2, Amazon S3 with Intelligent-Tiering, AWS Lambda, Amazon API Gateway, Amazon SQS, Amazon CloudFront, AWS WAF, AWS Shield Advanced, and AWS KMS. Infrastructure is defined as code using Terraform. Auto-scaling is configured for all compute components. Disaster Recovery: the platform achieves RTO < 4 hours and RPO < 1 hour via multi-AZ deployment and cross-region snapshots. DR runbooks are tested quarterly.

---

## 9. Non-Functional Requirements

| Category | NFR | Target |
|---|---|---|
| Performance | API Response Time | ≤ 200 ms at 95th percentile |
| Availability | Production Uptime SLA | 99.9% excluding planned maintenance |
| Availability | RTO | < 4 hours for P1 incidents |
| Availability | RPO | < 1 hour data loss |
| Scalability | Concurrent Users | 500 concurrent users |
| Security | Authentication | MFA enforced for all users |
| Security | Encryption in Transit | TLS 1.3 minimum |
| Security | Encryption at Rest | AES-256 via AWS KMS |
| Compliance | GDPR | Full UK GDPR and DPA 2018 compliance |
| Compliance | Data Minimisation | Only required personal data collected |
| Compliance | Accessibility | WCAG 2.2 AA |
| Reliability | Error Rate | < 0.1% of requests result in 5xx errors |
| Maintainability | Code Coverage | ≥ 80% unit test coverage |

---

## 10. Risk Register

| Risk ID | Description | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-001 | Data breach via misconfigured S3 bucket | Medium | High | AWS Config rules enforce block-public-access; Security Hub CIS benchmark alerts |
| R-002 | Insider threat — privileged user exfiltration | Low | Critical | CloudTrail + GuardDuty ML anomaly detection; JIT privileged access; quarterly access reviews |
| R-003 | Third-party supply-chain compromise | Low | High | SBOM generated per release; Snyk SCA in CI; approved vendor register; contractual security obligations |
| R-004 | DDoS attack disrupting public endpoints | Medium | High | AWS Shield Advanced; WAF rate limiting; CloudFront caching |
| R-005 | Credential stuffing on user portal | Medium | High | Cognito advanced security; MFA mandatory; account lockout after 5 failures |
| R-006 | RDS failover exceeds RPO/RTO | Low | Medium | Multi-AZ with automated failover; cross-region snapshot every 6 hours; quarterly DR rehearsal |
| R-007 | Regulatory non-compliance — GDPR Art. 17 erasure | Low | High | S3 Object Lock and Redshift partition delete supports erasure; DPO verified procedure |
| R-008 | Key compromise — AWS KMS CMK | Very Low | Critical | HSM-backed CMK; automatic 365-day rotation; CloudTrail alerts on key usage anomalies |
| R-009 | Phishing campaign targeting ECDP users | High | Medium | Defender for Office 365 ATP; mandatory phishing simulation every 6 months; SOC reporting procedure |
| R-010 | Certificate expiry causing service outage | Low | High | ACM auto-renewal for all public certs; CloudWatch alarm at 30 and 7 days before expiry |

---

## 11. Deployment Plan

Deployments use a blue-green strategy managed by AWS CodeDeploy. The pipeline is: feature branch → peer review → automated CI (Snyk SCA, SAST, unit tests, integration tests) → staging deploy → regression tests → production approval gate (two-person authorisation) → blue-green production deploy → CloudWatch canary verification → automatic rollback on alarm. All deployment runbooks are documented in Confluence and version-controlled in Git.

---

## 12. Compliance Statement

This solution fully complies with all applicable Defra security and technical architecture policies, including: the Defra Security Policy v4.1, the DDTS Architecture Guardrails v2.0, the Secure by Design framework, the GDS API Standards v2.1, and the GIO Integration Standards. All ten controls in the Defra Security Evaluation Checklist have been implemented as described in Section 4 of this document. Data protection compliance has been verified by the DPO (DPIA-ECDP-001). Security accreditation has been granted by the SIRO (SIRO-ACC-2026-004). Architecture approval has been granted by the ARB (ARB-2026-047) and the Solution Design Authority (SDA-2026-012).

---

## Appendix A: Architecture Decision Log

| Ref | Topic | Decision | Rationale |
|---|---|---|---|
| ADR-001 | Cloud Provider | AWS selected as primary cloud provider | Defra CDDO cloud-first mandate; existing AWS enterprise agreement |
| ADR-002 | Authentication | AWS Cognito with FIDO2 MFA for all users | MFA mandatory per Defra Security Policy |
| ADR-003 | Database | Amazon Aurora PostgreSQL Serverless v2 | ACID compliance; automatic scaling |
| ADR-004 | Encryption | AWS KMS CMKs with annual auto-rotation | Defra data classification policy mandates AES-256 |
| ADR-005 | Integration | GIO API Management Hub for all integrations | Defra architecture guardrail: no direct system-to-system calls |
| ADR-006 | Data Platform | Reuse Defra DASH platform for analytics | Avoid duplication; DASH already approved and GDPR-compliant |
| ADR-007 | Infrastructure as Code | Terraform with AWS CDK | Reproducibility; version-controlled; peer-reviewed before apply |
| ADR-008 | Logging | CloudTrail (7-year Object Lock) + CloudWatch | ISO 27001 A.12.4; GDPR accountability |
| ADR-009 | Deployment | Blue-green with automated rollback on alarms | Zero-downtime deployments; instant rollback |
| ADR-010 | Secret Management | AWS Secrets Manager with automatic rotation | No hardcoded secrets; rotation reduces breach window |

---

*End of Document*