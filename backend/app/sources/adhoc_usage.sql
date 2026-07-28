-- Ad Hoc Paperwork usage for one calendar month, by SENT date.
--
-- Snowflake, against the Keboola "Seso Prod" project (database SAPI_10112).
-- Kept as a file rather than a Python string so it can be pasted straight into
-- a Keboola SQL transformation or a worksheet and run unchanged when someone
-- needs to check a number by hand.
--
-- Verified 2026-07-27 by reproducing the app's known-good June 2026 extract:
-- FLC Disclosure 403, Georgia's W4 54, CA W-4 44, Plan 401(k) 28 all match, and
-- spot-checked packets resolve to the same worker, customer and contract name.
--
-- Two parameters, both inclusive ISO dates: :start_date and :end_date.
--
-- WHY THESE FILTERS
--
--   status = 'COMPLETE'                    "Completed ... packets only"
--   configuration_type IN (ANVIL_*)        "... Signature Packets ..."
--   deleted_at IS NULL                     soft-deleted packets are not usage
--
-- The two required content exclusions need no name matching, because they are
-- their own configuration_type in the source and are therefore already excluded
-- by the ANVIL_* filter:
--
--   HAMMER_FEDERAL_W4                  -> Federal W-4
--   SESO_INTERNAL_DISCIPLINARY_NOTICE  -> disciplinary notices
--
-- Matching those on paperwork_name instead would be fragile: 'Federal W-4' is a
-- display string that a customer could plausibly reuse, whereas the
-- configuration_type is structural. Also excluded by the same filter:
-- READ_ONLY_USER_CONFIGURED (not signed) and HAMMER_JOB_CONTRACT_SIGNATURE
-- (a job contract, billed separately).
--
-- Workers with no active contract are deliberately still returned;
-- has_active is reported so the app can show it, and the app's rule is to bill
-- them anyway. Filtering them out here would silently change that.

WITH "packets" AS (
    SELECT
        "p"."id"                             AS "packet_id",
        "p"."enterprise_worker_id"           AS "seso_worker_id",
        "p"."contract_id"                    AS "contract_id",
        "p"."preparer_user_uuid"             AS "preparer_user_uuid",
        "tc"."internal_name"                 AS "paperwork_name",
        -- Sent date decides the billing month. LA time, matching the
        -- period_basis setting the app has always used.
        CAST(CONVERT_TIMEZONE('America/Los_Angeles', "p"."created_at") AS DATE)          AS "sent_date",
        CAST(CONVERT_TIMEZONE('America/Los_Angeles', "p"."signed_by_worker_at") AS DATE) AS "signed_date"
    FROM "SAPI_10112"."in.c-seso_prod_input"."prod_ad_hoc_worker_packet" AS "p"
    JOIN "SAPI_10112"."in.c-seso_prod_input"."prod_ad_hoc_document_template_configuration" AS "tc"
      ON "tc"."id" = "p"."ad_hoc_document_template_configuration_id"
    WHERE "p"."deleted_at" IS NULL
      AND "p"."status" = 'COMPLETE'
      AND "tc"."configuration_type" IN ('ANVIL_USER_CONFIGURED', 'ANVIL_SESO_CONFIGURED')
      AND CAST(CONVERT_TIMEZONE('America/Los_Angeles', "p"."created_at") AS DATE)
            BETWEEN CAST(:start_date AS DATE) AND CAST(:end_date AS DATE)
),
-- One row per Salesforce account with a contracted Ad Hoc price. An account can
-- have the product on several Closed-Won opportunities; take the most recently
-- started, so a renewal at a new rate wins over last season's.
--
-- 'AdHoc_ReadOnly' is a different product and must not be picked up here.
"adhoc_price" AS (
    SELECT "account_id", "unit_price"
    FROM (
        SELECT
            "account_id",
            "unit_price",
            ROW_NUMBER() OVER (
                PARTITION BY "account_id"
                ORDER BY "sf_contract_start_date" DESC NULLS LAST, "opportunity_created_at" DESC
            ) AS "rn"
        FROM "SAPI_10112"."out.c-seso_salesforce_output"."salesforce_opportunity_product_cw"
        WHERE "opportunity_stage" = 'Closed Won'
          AND "sf_product_code" = 'AdHoc'
          AND "is_deleted" = FALSE
    )
    WHERE "rn" = 1
),
-- The enterprise -> Salesforce account mapping. One row per enterprise; if the
-- warehouse ever holds two, take one deterministically rather than fanning the
-- packet rows out into duplicates.
"sf_map" AS (
    SELECT "enterprise_id", "account_id", "account_name", "csm"
    FROM (
        SELECT
            "enterprise_id",
            "account_account_id_18_digit" AS "account_id",
            "account_account_name"        AS "account_name",
            NULLIF("account_csm", '-')    AS "csm",
            ROW_NUMBER() OVER (PARTITION BY "enterprise_id" ORDER BY "account_account_id_18_digit") AS "rn"
        FROM "SAPI_10112"."in.c-seso_salesforce_input"."sf_account_enterprises"
        WHERE "enterprise_id" IS NOT NULL
    )
    WHERE "rn" = 1
),
-- Does the worker hold a contract that covers the sent date? Reported, not
-- filtered on.
"active_contract" AS (
    SELECT DISTINCT "wc"."enterprise_worker_id"
    FROM "SAPI_10112"."in.c-seso_prod_input"."prod_worker_contract" AS "wc"
    WHERE "wc"."deleted_at" IS NULL
)
SELECT
    "e"."legal_name"                              AS "enterprise_name",
    COALESCE("m"."account_id", '')                AS "account_id",
    COALESCE("m"."csm", '')                       AS "csm",
    "ap"."unit_price"                             AS "sf_price",
    TRIM(COALESCE("w"."first_name", '') || ' ' || COALESCE("w"."last_name", '')) AS "worker_name",
    CAST("pk"."seso_worker_id" AS VARCHAR)        AS "seso_worker_id",
    "pk"."paperwork_name"                         AS "paperwork_name",
    CAST("pk"."packet_id" AS VARCHAR)             AS "packet_id",
    TO_CHAR("pk"."sent_date",   'YYYY-MM-DD')     AS "sent_date",
    TO_CHAR("pk"."signed_date", 'YYYY-MM-DD')     AS "signed_date",
    COALESCE(TRIM(COALESCE("u"."first_name", '') || ' ' || COALESCE("u"."last_name", '')), '') AS "sender_name",
    COALESCE("pk"."contract_id", '')              AS "contract_ids",
    COALESCE("hc"."name", '')                     AS "contract_name",
    CASE WHEN "ac"."enterprise_worker_id" IS NOT NULL THEN 1 ELSE 0 END AS "has_active"
FROM "packets" AS "pk"
LEFT JOIN "SAPI_10112"."in.c-seso_prod_input"."prod_enterprise_worker" AS "w"
       ON "w"."id" = "pk"."seso_worker_id"
LEFT JOIN "SAPI_10112"."in.c-seso_prod_input"."prod_enterprise" AS "e"
       ON "e"."id" = "w"."enterprise_id"
LEFT JOIN "sf_map" AS "m"
       ON "m"."enterprise_id" = "w"."enterprise_id"
LEFT JOIN "adhoc_price" AS "ap"
       ON "ap"."account_id" = "m"."account_id"
-- Join on contract_id, not uuid: prod_h2a_contract has both and only
-- contract_id matches the packet's reference.
LEFT JOIN "SAPI_10112"."in.c-seso_prod_input"."prod_h2a_contract" AS "hc"
       ON "hc"."contract_id" = "pk"."contract_id"
LEFT JOIN "SAPI_10112"."in.c-seso_prod_input"."prod_user" AS "u"
       ON "u"."uuid" = "pk"."preparer_user_uuid"
LEFT JOIN "active_contract" AS "ac"
       ON "ac"."enterprise_worker_id" = "pk"."seso_worker_id"
WHERE "e"."legal_name" IS NOT NULL
ORDER BY "enterprise_name", "sent_date", "packet_id"
