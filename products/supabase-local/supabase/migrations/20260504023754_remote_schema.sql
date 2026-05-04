


SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


CREATE SCHEMA IF NOT EXISTS "core";


ALTER SCHEMA "core" OWNER TO "postgres";


CREATE EXTENSION IF NOT EXISTS "pg_cron" WITH SCHEMA "pg_catalog";






CREATE SCHEMA IF NOT EXISTS "financial_analytics";


ALTER SCHEMA "financial_analytics" OWNER TO "postgres";


CREATE SCHEMA IF NOT EXISTS "governance";


ALTER SCHEMA "governance" OWNER TO "postgres";


CREATE SCHEMA IF NOT EXISTS "p2p_audit";


ALTER SCHEMA "p2p_audit" OWNER TO "postgres";


CREATE SCHEMA IF NOT EXISTS "p2p_core";


ALTER SCHEMA "p2p_core" OWNER TO "postgres";


COMMENT ON SCHEMA "public" IS 'standard public schema';



CREATE SCHEMA IF NOT EXISTS "sec";


ALTER SCHEMA "sec" OWNER TO "postgres";


CREATE SCHEMA IF NOT EXISTS "sem";


ALTER SCHEMA "sem" OWNER TO "postgres";


CREATE SCHEMA IF NOT EXISTS "staging";


ALTER SCHEMA "staging" OWNER TO "postgres";


CREATE EXTENSION IF NOT EXISTS "pg_stat_statements" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "pgcrypto" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "supabase_vault" WITH SCHEMA "vault";






CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA "extensions";






CREATE OR REPLACE FUNCTION "core"."can_access_tenant"("p_tenant_id" "uuid") RETURNS boolean
    LANGUAGE "sql" STABLE SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
  select
    p_tenant_id is not null
    and core.current_tenant_id() is not null
    and p_tenant_id = core.current_tenant_id()
    and core.is_tenant_member(p_tenant_id);
$$;


ALTER FUNCTION "core"."can_access_tenant"("p_tenant_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "core"."current_anonymization_secret"() RETURNS "text"
    LANGUAGE "sql" STABLE SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
  select coalesce(
    nullif(current_setting('app.settings.anonymization_secret', true), ''),
    'CHANGE_ME_IN_PROD'
  );
$$;


ALTER FUNCTION "core"."current_anonymization_secret"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "core"."current_tenant_id"() RETURNS "uuid"
    LANGUAGE "sql" STABLE
    AS $$
  select coalesce(
    nullif(auth.jwt() ->> 'current_tenant_id', '')::uuid,
    nullif(auth.jwt() -> 'app_metadata' ->> 'current_tenant_id', '')::uuid,
    nullif(auth.jwt() -> 'raw_app_meta_data' ->> 'current_tenant_id', '')::uuid
  );
$$;


ALTER FUNCTION "core"."current_tenant_id"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "core"."current_user_id"() RETURNS "uuid"
    LANGUAGE "sql" STABLE
    AS $$
  select auth.uid();
$$;


ALTER FUNCTION "core"."current_user_id"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "core"."fn_update_audit_timestamps"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


ALTER FUNCTION "core"."fn_update_audit_timestamps"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "core"."get_user_context"("p_tenant_id" "uuid", OUT "v_role" "text", OUT "v_vendor_id" "uuid") RETURNS "record"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
    SELECT membership_role, vendor_id 
    INTO v_role, v_vendor_id
    FROM core.user_tenants
    WHERE user_id = auth.uid() AND tenant_id = p_tenant_id AND is_active = true
    LIMIT 1;
END;
$$;


ALTER FUNCTION "core"."get_user_context"("p_tenant_id" "uuid", OUT "v_role" "text", OUT "v_vendor_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "core"."is_tenant_member"("p_tenant_id" "uuid") RETURNS boolean
    LANGUAGE "sql" STABLE SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
  select exists (
    select 1
    from core.user_tenants ut
    join core.tenants t
      on t.id = ut.tenant_id
    where ut.user_id = auth.uid()
      and ut.tenant_id = p_tenant_id
      and ut.is_active = true
      and t.is_active = true
  );
$$;


ALTER FUNCTION "core"."is_tenant_member"("p_tenant_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "core"."set_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
begin
  new.updated_at = now();
  return new;
end;
$$;


ALTER FUNCTION "core"."set_updated_at"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "governance"."hash_supplier_hmac"("p_supplier_name" "text", "p_vendor_code" "text" DEFAULT NULL::"text") RETURNS "text"
    LANGUAGE "sql" STABLE SECURITY DEFINER
    AS $$
  select encode(
    -- Forzamos que el tercer parámetro sea reconocido como text con ::text
    hmac(
      (coalesce(trim(p_supplier_name), '') || '|' || coalesce(trim(p_vendor_code), ''))::bytea,
      core.current_anonymization_secret()::bytea,
      'sha256'::text 
    ),
    'hex'
  );
$$;


ALTER FUNCTION "governance"."hash_supplier_hmac"("p_supplier_name" "text", "p_vendor_code" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_audit"."log_changes"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        INSERT INTO p2p_audit.audit_logs (table_name, record_id, action, old_data)
        VALUES (TG_TABLE_NAME, OLD.invoice_id, 'DELETE', row_to_json(OLD)::jsonb);
        RETURN OLD;
    ELSIF (TG_OP = 'UPDATE') THEN
        INSERT INTO p2p_audit.audit_logs (table_name, record_id, action, old_data, new_data)
        VALUES (TG_TABLE_NAME, NEW.invoice_id, 'UPDATE', row_to_json(OLD)::jsonb, row_to_json(NEW)::jsonb);
        RETURN NEW;
    END IF;
    RETURN NEW;
END;
$$;


ALTER FUNCTION "p2p_audit"."log_changes"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."allowed_correction_actions"("p_exception_table" "text", "p_exception_code" "text") RETURNS "text"[]
    LANGUAGE "sql" STABLE
    AS $$
  select case
    when p_exception_table = 'document_reception_exceptions'
      and p_exception_code = 'SII_PRESENT_SAP_MISSING'
      then array['REQUEST_SUPPLIER_RESEND']
    when p_exception_table = 'relational_integrity_exceptions'
      and p_exception_code in ('GR_MISSING_PO_REFERENCE', 'GR_INVALID_PO_REFERENCE')
      then array['LINK_GR_TO_PO']
    when p_exception_table = 'relational_integrity_exceptions'
      and p_exception_code in ('INVOICE_MISSING_GR_REFERENCE', 'INVOICE_INVALID_GR_REFERENCE')
      then array['LINK_INVOICE_TO_GR']
    when p_exception_table = 'p2p_reconciliation_exceptions'
      then array['REVIEW_REQUIRED']
    else array['REVIEW_REQUIRED']
  end;
$$;


ALTER FUNCTION "p2p_core"."allowed_correction_actions"("p_exception_table" "text", "p_exception_code" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."can_edit_case_status"("p_status" "text") RETURNS boolean
    LANGUAGE "sql" STABLE
    AS $$
  select p_status in ('OPEN','EXPORTED','IN_CORRECTION','SUBMITTED');
$$;


ALTER FUNCTION "p2p_core"."can_edit_case_status"("p_status" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."derive_exception_priority"("p_exception_table" "text", "p_exception_code" "text", "p_gap_amount_local" numeric DEFAULT NULL::numeric) RETURNS "text"
    LANGUAGE "sql" STABLE
    AS $$
  select case
    when p_exception_table = 'document_reception_exceptions' then 'HIGH'
    when p_exception_code in ('GR_MISSING_PO_REFERENCE', 'GR_INVALID_PO_REFERENCE', 'INVOICE_MISSING_GR_REFERENCE', 'INVOICE_INVALID_GR_REFERENCE') then 'HIGH'
    when p_exception_code = 'AMOUNT_MISMATCH' and abs(coalesce(p_gap_amount_local,0)) >= 100000 then 'CRITICAL'
    when p_exception_code = 'AMOUNT_MISMATCH' and abs(coalesce(p_gap_amount_local,0)) >= 10000 then 'HIGH'
    when p_exception_code in ('SUPPLIER_MISMATCH','DUPLICATE_CANDIDATE','MULTIPLE_MATCH_CANDIDATE') then 'HIGH'
    when p_exception_code in ('CURRENCY_MISMATCH','DATE_SEQUENCE_ANOMALY','REVIEW_REQUIRED') then 'MEDIUM'
    else 'MEDIUM'
  end;
$$;


ALTER FUNCTION "p2p_core"."derive_exception_priority"("p_exception_table" "text", "p_exception_code" "text", "p_gap_amount_local" numeric) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."fn_anonymize_old_vendors"("retention_years" integer DEFAULT 6) RETURNS integer
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    anonymized_count INT;
BEGIN
    WITH targets AS (
        -- Identificar proveedores cuya última factura emitida supere los años de retención
        -- o que hayan sido creados hace ese tiempo y nunca hayan emitido facturas.
        SELECT v.id
        FROM p2p_core.vendors v
        LEFT JOIN p2p_core.sii_invoice_headers h ON v.id = h.vendor_id
        GROUP BY v.id, v.created_at
        HAVING MAX(h.issue_date) < (CURRENT_DATE - (retention_years || ' years')::interval)
           OR (MAX(h.issue_date) IS NULL AND v.created_at < (CURRENT_DATE - (retention_years || ' years')::interval))
    )
    UPDATE p2p_core.vendors
    SET 
        -- 1. Eliminamos el nombre real
        vendor_name = 'PROVEEDOR ANONIMIZADO',
        -- 2. Destruimos el RUT real pero mantenemos la unicidad requerida por la tabla
        vendor_rut = 'ANON-' || SUBSTRING(id::text, 1, 8), 
        -- 3. Borramos otros posibles datos identificatorios en el futuro si agregas más columnas (ej. email, teléfono)
        is_active = false,
        updated_at = NOW()
    WHERE id IN (SELECT id FROM targets)
    AND vendor_name != 'PROVEEDOR ANONIMIZADO'; -- Evitar re-procesar los ya anonimizados

    -- Obtener la cantidad de filas afectadas para propósitos de auditoría/logs
    GET DIAGNOSTICS anonymized_count = ROW_COUNT;
    
    RETURN anonymized_count;
END;
$$;


ALTER FUNCTION "p2p_core"."fn_anonymize_old_vendors"("retention_years" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."fn_mask_name"("name_input" "text") RETURNS "text"
    LANGUAGE "plpgsql" IMMUTABLE
    AS $$
BEGIN
  IF name_input IS NULL OR LENGTH(name_input) < 3 THEN
    RETURN '***';
  END IF;
  RETURN SUBSTRING(name_input, 1, 1) || '****' || SUBSTRING(name_input, LENGTH(name_input), 1);
END;
$$;


ALTER FUNCTION "p2p_core"."fn_mask_name"("name_input" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."fn_mask_rut"("rut_input" "text") RETURNS "text"
    LANGUAGE "plpgsql" IMMUTABLE
    AS $$
BEGIN
  IF rut_input IS NULL OR LENGTH(rut_input) < 8 THEN
    RETURN rut_input;
  END IF;
  -- Muestra los primeros 2 dígitos, enmascara el centro, y deja el guion y el dígito verificador
  RETURN SUBSTRING(rut_input, 1, 2) || '****' || SUBSTRING(rut_input, LENGTH(rut_input) - 1, 2);
END;
$$;


ALTER FUNCTION "p2p_core"."fn_mask_rut"("rut_input" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."fn_standardize_rut"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  IF NEW.vendor_rut IS NOT NULL THEN
    -- A) Eliminar puntos, espacios, guiones existentes y pasar a mayúscula
    NEW.vendor_rut := UPPER(REPLACE(REPLACE(REPLACE(NEW.vendor_rut, '.', ''), ' ', ''), '-', ''));
    
    -- B) Insertar el guion obligatorio antes del último caracter
    IF LENGTH(NEW.vendor_rut) >= 2 THEN
        NEW.vendor_rut := SUBSTRING(NEW.vendor_rut, 1, LENGTH(NEW.vendor_rut) - 1) || '-' || SUBSTRING(NEW.vendor_rut, LENGTH(NEW.vendor_rut), 1);
    END IF;
  END IF;
  RETURN NEW;
END;
$$;


ALTER FUNCTION "p2p_core"."fn_standardize_rut"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."is_valid_correction_action"("p_exception_table" "text", "p_exception_code" "text", "p_action" "text") RETURNS boolean
    LANGUAGE "sql" STABLE
    AS $$
  select p_action = any(p2p_core.allowed_correction_actions(p_exception_table, p_exception_code));
$$;


ALTER FUNCTION "p2p_core"."is_valid_correction_action"("p_exception_table" "text", "p_exception_code" "text", "p_action" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."make_row_hash"("p_exception_table" "text", "p_exception_id" "uuid", "p_target_value" "text" DEFAULT NULL::"text", "p_client_reference" "text" DEFAULT NULL::"text") RETURNS "text"
    LANGUAGE "sql" STABLE SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
  select encode(
    extensions.hmac( -- <-- EL CAMBIO ESTÁ AQUÍ (Ruta explícita)
      (
        coalesce(p_exception_table, '') || '|' ||
        coalesce(p_exception_id::text, '') || '|' ||
        coalesce(trim(p_target_value), '') || '|' ||
        coalesce(trim(p_client_reference), '')
      )::bytea,
      core.current_anonymization_secret()::bytea,
      'sha256'::text
    ),
    'hex'
  );
$$;


ALTER FUNCTION "p2p_core"."make_row_hash"("p_exception_table" "text", "p_exception_id" "uuid", "p_target_value" "text", "p_client_reference" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."normalized_text"("p_value" "text") RETURNS "text"
    LANGUAGE "sql" IMMUTABLE
    AS $$
  select nullif(regexp_replace(lower(trim(coalesce(p_value, ''))), '\s+', ' ', 'g'), '');
$$;


ALTER FUNCTION "p2p_core"."normalized_text"("p_value" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."rpc_process_dte"("p_staging_id" "uuid") RETURNS boolean
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    v_record RECORD;
    v_vendor_id UUID;
    v_vendor_rut TEXT;
    v_vendor_name TEXT;
    v_folio BIGINT;
    v_dte_type INTEGER;
    v_header_id UUID;
    v_line JSONB;
BEGIN
    FOR v_record IN 
        SELECT payload_xml, tenant_id, status 
        FROM staging.raw_dte_inbound
        WHERE id = p_staging_id
    LOOP
        IF v_record.status = 'processed' THEN
            RETURN FALSE;
        END IF;

        v_vendor_rut := v_record.payload_xml->>'RUTEmisor';
        v_vendor_name := v_record.payload_xml->>'RznSoc';
        v_folio := (v_record.payload_xml->>'Folio')::BIGINT;
        v_dte_type := (v_record.payload_xml->>'TipoDTE')::INTEGER;

        INSERT INTO p2p_core.vendors (tenant_id, vendor_code, vendor_name, vendor_rut)
        VALUES (v_record.tenant_id, v_vendor_rut, COALESCE(v_vendor_name, 'Sin Nombre'), v_vendor_rut)
        ON CONFLICT (tenant_id, vendor_rut) 
        DO UPDATE SET vendor_name = EXCLUDED.vendor_name
        RETURNING id INTO v_vendor_id;

        INSERT INTO p2p_core.sii_invoice_headers (
            tenant_id, vendor_id, dte_type, folio, issue_date, total_amount, currency
        )
        VALUES (
            v_record.tenant_id,
            v_vendor_id,
            v_dte_type,
            v_folio,
            (v_record.payload_xml->>'FchEmis')::DATE,
            (v_record.payload_xml->>'MntTotal')::NUMERIC,
            COALESCE(v_record.payload_xml->>'Moneda', 'CLP')
        )
        ON CONFLICT (tenant_id, vendor_id, dte_type, folio)
        DO UPDATE SET 
            total_amount = EXCLUDED.total_amount,
            updated_at = NOW()
        RETURNING id INTO v_header_id;

        DELETE FROM p2p_core.sii_invoice_lines WHERE header_id = v_header_id;

        FOR v_line IN SELECT * FROM jsonb_array_elements(v_record.payload_xml->'Detalle')
        LOOP
            INSERT INTO p2p_core.sii_invoice_lines (
                tenant_id, header_id, line_number, item_name, qty, unit_price, line_amount
            )
            VALUES (
                v_record.tenant_id,
                v_header_id,
                (v_line->>'NroLinDet')::INTEGER,
                v_line->>'NmbItem',
                (v_line->>'QtyItem')::NUMERIC,
                (v_line->>'PrcItem')::NUMERIC,
                (v_line->>'MontoItem')::NUMERIC
            );
        END LOOP;

        UPDATE staging.raw_dte_inbound 
        SET status = 'processed', updated_at = NOW() 
        WHERE id = p_staging_id;

        RETURN TRUE;
    END LOOP;

    RETURN FALSE;

EXCEPTION WHEN OTHERS THEN
    UPDATE staging.raw_dte_inbound
    SET status = 'error', error_log = SQLERRM, updated_at = NOW()
    WHERE id = p_staging_id;
    
    RAISE;
END;
$$;


ALTER FUNCTION "p2p_core"."rpc_process_dte"("p_staging_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."rpc_process_oc"("p_staging_id" "uuid") RETURNS boolean
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    v_record RECORD;
    v_vendor_id UUID;
    v_vendor_rut TEXT;
    v_vendor_name TEXT;
    v_doc_num INTEGER;
    v_sap_doc_entry INTEGER;
    v_total_amount NUMERIC;
    v_currency TEXT;
BEGIN
    -- Leer el registro desde Staging
    FOR v_record IN 
        SELECT payload, tenant_id, status 
        FROM staging.raw_oc_inbound
        WHERE id = p_staging_id
    LOOP
        -- Evitar doble procesamiento
        IF v_record.status = 'processed' THEN
            RETURN FALSE;
        END IF;

        -- 1. Mapear datos desde el JSON (n8n enviará las columnas del CSV así)
        v_vendor_rut := v_record.payload->>'RutProveedor';
        v_vendor_name := v_record.payload->>'NombreProveedor';
        v_doc_num := (v_record.payload->>'NumeroOC')::INTEGER;
        v_total_amount := (v_record.payload->>'MontoTotal')::NUMERIC;
        v_currency := COALESCE(v_record.payload->>'Moneda', 'CLP');
        
        -- En SAP, el DocEntry es la llave interna. Si no viene en el CSV, 
        -- hacemos un fallback al número de documento para cumplir el Constraint NOT NULL.
        v_sap_doc_entry := (v_record.payload->>'SAPDocEntry')::INTEGER;
        IF v_sap_doc_entry IS NULL THEN
            v_sap_doc_entry := v_doc_num;
        END IF;

        -- 2. UPSERT de Proveedor
        INSERT INTO p2p_core.vendors (tenant_id, vendor_code, vendor_name, vendor_rut)
        VALUES (v_record.tenant_id, v_vendor_rut, COALESCE(v_vendor_name, 'Sin Nombre'), v_vendor_rut)
        ON CONFLICT (tenant_id, vendor_rut) 
        DO UPDATE SET vendor_name = EXCLUDED.vendor_name
        RETURNING id INTO v_vendor_id;

        -- 3. UPSERT de Orden de Compra
        INSERT INTO p2p_core.purchase_orders (
            tenant_id, vendor_id, doc_num, sap_doc_entry, document_date, 
            total_amount, currency, exchange_rate, source_system
        )
        VALUES (
            v_record.tenant_id,
            v_vendor_id,
            v_doc_num,
            v_sap_doc_entry,
            (v_record.payload->>'FechaOC')::DATE,
            v_total_amount,
            v_currency,
            1.0, -- Tasa de cambio base
            'CSV_UPLOAD'
        )
        ON CONFLICT (tenant_id, doc_num)
        DO UPDATE SET 
            total_amount = EXCLUDED.total_amount,
            sap_doc_entry = EXCLUDED.sap_doc_entry,
            updated_at = NOW();

        -- 4. Marcar como exitoso en Staging
        UPDATE staging.raw_oc_inbound 
        SET status = 'processed', updated_at = NOW() 
        WHERE id = p_staging_id;

        RETURN TRUE;
    END LOOP;

    RETURN FALSE;

EXCEPTION WHEN OTHERS THEN
    -- Ante error, rollback automático en core y registro forense en staging
    UPDATE staging.raw_oc_inbound
    SET status = 'error', error_log = SQLERRM, updated_at = NOW()
    WHERE id = p_staging_id;
    
    RAISE;
END;
$$;


ALTER FUNCTION "p2p_core"."rpc_process_oc"("p_staging_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "p2p_core"."validate_correction_upload_row"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
declare
  v_exception_code text;
  v_exception_status text;
  v_allowed boolean;
begin
  -- Resolve exception code/status from the respective exception table
  if new.exception_table = 'document_reception_exceptions' then
    select exception_code, exception_status
      into v_exception_code, v_exception_status
    from p2p_core.document_reception_exceptions
    where tenant_id = new.tenant_id
      and id = new.exception_id;

  elsif new.exception_table = 'relational_integrity_exceptions' then
    select exception_code, exception_status
      into v_exception_code, v_exception_status
    from p2p_core.relational_integrity_exceptions
    where tenant_id = new.tenant_id
      and id = new.exception_id;

  elsif new.exception_table = 'p2p_reconciliation_exceptions' then
    select exception_code, exception_status
      into v_exception_code, v_exception_status
    from p2p_core.p2p_reconciliation_exceptions
    where tenant_id = new.tenant_id
      and id = new.exception_id;

  else
    raise exception 'Unknown exception_table: %', new.exception_table;
  end if;

  if v_exception_code is null then
    raise exception 'Exception % not found for tenant %', new.exception_id, new.tenant_id;
  end if;

  if not p2p_core.can_edit_case_status(v_exception_status) then
    raise exception 'Exception % is not editable in status %', new.exception_id, v_exception_status;
  end if;

  v_allowed := p2p_core.is_valid_correction_action(
    new.exception_table,
    v_exception_code,
    new.correction_action
  );

  if not v_allowed then
    raise exception 'Correction action % is not valid for % / %',
      new.correction_action, new.exception_table, v_exception_code;
  end if;

  if new.correction_action in ('LINK_INVOICE_TO_GR','LINK_GR_TO_PO')
     and coalesce(trim(new.proposed_reference_value), '') = '' then
    raise exception 'proposed_reference_value is required for action %', new.correction_action;
  end if;

  if new.correction_action = 'REQUEST_SUPPLIER_RESEND'
     and coalesce(trim(new.client_comment), '') = '' then
    raise exception 'client_comment is required for REQUEST_SUPPLIER_RESEND';
  end if;

  new.row_hash := p2p_core.make_row_hash(
    new.exception_table,
    new.exception_id,
    new.proposed_reference_value,
    new.client_reference
  );

  return new;
end;
$$;


ALTER FUNCTION "p2p_core"."validate_correction_upload_row"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_tenant_id"() RETURNS "uuid"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'public', 'core'
    AS $$
DECLARE
    v_tenant_id uuid;
BEGIN
    SELECT tenant_id INTO v_tenant_id
    FROM core.tenant_users
    WHERE user_id = auth.uid() -- auth.uid() es una función nativa segura
    LIMIT 1;

    RETURN v_tenant_id;
END;
$$;


ALTER FUNCTION "public"."get_tenant_id"() OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "core"."exchange_rates" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "rate_date" "date" NOT NULL,
    "currency_code" character varying(3) NOT NULL,
    "rate_value" numeric(18,4) NOT NULL,
    "source" character varying(50) DEFAULT 'CMF'::character varying,
    "created_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "core"."exchange_rates" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "core"."tenants" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_code" character varying(50) NOT NULL,
    "tenant_name" "text" NOT NULL,
    "country_code" character varying(2) DEFAULT 'CL'::character varying NOT NULL,
    "base_currency" character varying(3) DEFAULT 'CLP'::character varying NOT NULL,
    "is_active" boolean DEFAULT true NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "uuid",
    CONSTRAINT "ck_tenants_base_currency" CHECK ((("char_length"(("base_currency")::"text") = 3) AND (("base_currency")::"text" = "upper"(("base_currency")::"text")))),
    CONSTRAINT "ck_tenants_country_code" CHECK ((("char_length"(("country_code")::"text") = 2) AND (("country_code")::"text" = "upper"(("country_code")::"text")))),
    CONSTRAINT "ck_tenants_tenant_code_not_blank" CHECK (("btrim"(("tenant_code")::"text") <> ''::"text")),
    CONSTRAINT "ck_tenants_tenant_name_not_blank" CHECK (("btrim"("tenant_name") <> ''::"text"))
);


ALTER TABLE "core"."tenants" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "core"."user_tenants" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "membership_role" "text" DEFAULT 'member'::"text" NOT NULL,
    "is_default" boolean DEFAULT false NOT NULL,
    "is_active" boolean DEFAULT true NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "uuid",
    "vendor_id" "uuid",
    CONSTRAINT "chk_user_tenants_valid_roles" CHECK (("membership_role" = ANY (ARRAY['cognity_admin'::"text", 'cognity_analyst'::"text", 'client_admin'::"text", 'client_user'::"text", 'vendor_user'::"text"]))),
    CONSTRAINT "chk_vendor_user_requires_vendor_id" CHECK (((("membership_role" = 'vendor_user'::"text") AND ("vendor_id" IS NOT NULL)) OR (("membership_role" <> 'vendor_user'::"text") AND ("vendor_id" IS NULL)))),
    CONSTRAINT "ck_user_tenants_role_not_blank" CHECK (("btrim"("membership_role") <> ''::"text"))
);


ALTER TABLE "core"."user_tenants" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."goods_receipts" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "po_id" "uuid" NOT NULL,
    "vendor_id" "uuid" NOT NULL,
    "sap_doc_entry" integer NOT NULL,
    "gr_number" integer NOT NULL,
    "document_date" timestamp with time zone,
    "posting_date" timestamp with time zone,
    "amount_received" numeric(19,6) NOT NULL,
    "currency" character varying(3) DEFAULT 'CLP'::character varying NOT NULL,
    "exchange_rate" numeric(10,4) DEFAULT 1.0 NOT NULL,
    "status_code" "text",
    "source_system" "text" DEFAULT 'SAP_B1'::"text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "uuid",
    CONSTRAINT "ck_goods_receipts_amount_received" CHECK (("amount_received" >= (0)::numeric)),
    CONSTRAINT "ck_goods_receipts_currency" CHECK ((("char_length"(("currency")::"text") = 3) AND (("currency")::"text" = "upper"(("currency")::"text")))),
    CONSTRAINT "ck_goods_receipts_exchange_rate" CHECK (("exchange_rate" > (0)::numeric)),
    CONSTRAINT "ck_goods_receipts_source_system_not_blank" CHECK (("btrim"("source_system") <> ''::"text"))
);


ALTER TABLE "p2p_core"."goods_receipts" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."invoices" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "gr_id" "uuid" NOT NULL,
    "vendor_id" "uuid" NOT NULL,
    "sap_doc_entry" integer NOT NULL,
    "invoice_number" integer NOT NULL,
    "document_date" timestamp with time zone,
    "posting_date" timestamp with time zone,
    "due_date" timestamp with time zone,
    "total_amount" numeric(19,6) NOT NULL,
    "tax_amount" numeric(19,6),
    "currency" character varying(3) DEFAULT 'CLP'::character varying NOT NULL,
    "exchange_rate" numeric(10,4) DEFAULT 1.0 NOT NULL,
    "status_code" "text",
    "source_system" "text" DEFAULT 'SAP_B1'::"text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "uuid",
    CONSTRAINT "ck_invoices_currency" CHECK ((("char_length"(("currency")::"text") = 3) AND (("currency")::"text" = "upper"(("currency")::"text")))),
    CONSTRAINT "ck_invoices_exchange_rate" CHECK (("exchange_rate" > (0)::numeric)),
    CONSTRAINT "ck_invoices_source_system_not_blank" CHECK (("btrim"("source_system") <> ''::"text")),
    CONSTRAINT "ck_invoices_tax_amount" CHECK ((("tax_amount" IS NULL) OR ("tax_amount" >= (0)::numeric))),
    CONSTRAINT "ck_invoices_total_amount" CHECK (("total_amount" >= (0)::numeric))
);


ALTER TABLE "p2p_core"."invoices" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."purchase_orders" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "vendor_id" "uuid" NOT NULL,
    "sap_doc_entry" integer NOT NULL,
    "doc_num" integer NOT NULL,
    "document_date" timestamp with time zone,
    "posting_date" timestamp with time zone,
    "due_date" timestamp with time zone,
    "total_amount" numeric(19,6) NOT NULL,
    "currency" character varying(3) DEFAULT 'CLP'::character varying NOT NULL,
    "exchange_rate" numeric(10,4) DEFAULT 1.0 NOT NULL,
    "status_code" "text",
    "source_system" "text" DEFAULT 'SAP_B1'::"text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "uuid",
    CONSTRAINT "ck_purchase_orders_currency" CHECK ((("char_length"(("currency")::"text") = 3) AND (("currency")::"text" = "upper"(("currency")::"text")))),
    CONSTRAINT "ck_purchase_orders_exchange_rate" CHECK (("exchange_rate" > (0)::numeric)),
    CONSTRAINT "ck_purchase_orders_source_system_not_blank" CHECK (("btrim"("source_system") <> ''::"text")),
    CONSTRAINT "ck_purchase_orders_total_amount" CHECK (("total_amount" >= (0)::numeric))
);


ALTER TABLE "p2p_core"."purchase_orders" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."vendors" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "vendor_code" "text" NOT NULL,
    "vendor_name" "text" NOT NULL,
    "vendor_rut" "text",
    "industry_sector" "text",
    "country_code" character varying(2) DEFAULT 'CL'::character varying NOT NULL,
    "is_active" boolean DEFAULT true NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_by" "uuid",
    CONSTRAINT "chk_vendor_rut_format" CHECK ((("vendor_rut" IS NULL) OR ("vendor_rut" ~ '^[1-9]{7,8}-[0-9K]$'::"text"))),
    CONSTRAINT "ck_vendors_country_code" CHECK ((("char_length"(("country_code")::"text") = 2) AND (("country_code")::"text" = "upper"(("country_code")::"text")))),
    CONSTRAINT "ck_vendors_vendor_code_not_blank" CHECK (("btrim"("vendor_code") <> ''::"text")),
    CONSTRAINT "ck_vendors_vendor_name_not_blank" CHECK (("btrim"("vendor_name") <> ''::"text"))
);


ALTER TABLE "p2p_core"."vendors" OWNER TO "postgres";


CREATE OR REPLACE VIEW "sem"."vw_blind_money_p2p" WITH ("security_invoker"='true') AS
 SELECT "po"."tenant_id",
    "t"."tenant_code",
    "t"."tenant_name",
    "t"."base_currency",
    "po"."id" AS "purchase_order_id",
    "gr"."id" AS "goods_receipt_id",
    "i"."id" AS "invoice_id",
    "v"."id" AS "vendor_id",
    "v"."vendor_code",
    "v"."vendor_name",
    "v"."industry_sector",
    "po"."doc_num" AS "purchase_order_number",
    "gr"."gr_number" AS "goods_receipt_number",
    "i"."invoice_number",
    COALESCE("i"."document_date", "gr"."document_date", "po"."document_date") AS "audit_date",
    "po"."currency" AS "purchase_order_currency",
    "po"."exchange_rate" AS "purchase_order_exchange_rate",
    "po"."total_amount" AS "purchase_order_total_amount",
    "round"(("po"."total_amount" * "po"."exchange_rate"), 6) AS "purchase_order_total_amount_local",
    "gr"."currency" AS "goods_receipt_currency",
    "gr"."exchange_rate" AS "goods_receipt_exchange_rate",
    "gr"."amount_received" AS "goods_receipt_amount",
    "round"(("gr"."amount_received" * "gr"."exchange_rate"), 6) AS "goods_receipt_amount_local",
    "i"."currency" AS "invoice_currency",
    "i"."exchange_rate" AS "invoice_exchange_rate",
    "i"."total_amount" AS "invoice_total_amount",
    "round"(("i"."total_amount" * "i"."exchange_rate"), 6) AS "invoice_total_amount_local",
    "round"((("i"."total_amount" * "i"."exchange_rate") - ("gr"."amount_received" * "gr"."exchange_rate")), 6) AS "blind_money_gap_amount_local",
    "abs"("round"((("i"."total_amount" * "i"."exchange_rate") - ("gr"."amount_received" * "gr"."exchange_rate")), 6)) AS "blind_money_gap_amount_abs_local",
        CASE
            WHEN ("round"((("i"."total_amount" * "i"."exchange_rate") - ("gr"."amount_received" * "gr"."exchange_rate")), 6) > (2)::numeric) THEN 'OVER_BILLED'::"text"
            WHEN ("round"((("i"."total_amount" * "i"."exchange_rate") - ("gr"."amount_received" * "gr"."exchange_rate")), 6) < ('-2'::integer)::numeric) THEN 'UNDER_BILLED'::"text"
            ELSE 'WITHIN_TOLERANCE'::"text"
        END AS "blind_money_gap_status"
   FROM (((("p2p_core"."purchase_orders" "po"
     JOIN "p2p_core"."goods_receipts" "gr" ON ((("gr"."tenant_id" = "po"."tenant_id") AND ("gr"."po_id" = "po"."id"))))
     JOIN "p2p_core"."invoices" "i" ON ((("i"."tenant_id" = "gr"."tenant_id") AND ("i"."gr_id" = "gr"."id"))))
     JOIN "p2p_core"."vendors" "v" ON ((("v"."tenant_id" = "po"."tenant_id") AND ("v"."id" = "po"."vendor_id"))))
     JOIN "core"."tenants" "t" ON (("t"."id" = "po"."tenant_id")));


ALTER VIEW "sem"."vw_blind_money_p2p" OWNER TO "postgres";


CREATE OR REPLACE VIEW "governance"."vw_anonymized_audit" WITH ("security_invoker"='true') AS
 SELECT "tenant_id",
    "base_currency",
    ("date_trunc"('month'::"text", "audit_date"))::"date" AS "audit_month",
    "industry_sector",
    "governance"."hash_supplier_hmac"("vendor_name", "vendor_code") AS "supplier_hash",
    "purchase_order_id",
    "goods_receipt_id",
    "invoice_id",
    "purchase_order_total_amount_local",
    "goods_receipt_amount_local",
    "invoice_total_amount_local",
    "blind_money_gap_amount_local",
    "blind_money_gap_amount_abs_local",
    "blind_money_gap_status"
   FROM "sem"."vw_blind_money_p2p" "s";


ALTER VIEW "governance"."vw_anonymized_audit" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."document_reception_exceptions" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "reconciliation_run_id" "uuid" NOT NULL,
    "sii_invoice_header_id" "uuid" NOT NULL,
    "exception_code" "text" NOT NULL,
    "exception_status" "text" DEFAULT 'OPEN'::"text" NOT NULL,
    "suggested_action" "text",
    "detected_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "p2p_core"."document_reception_exceptions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."p2p_reconciliation_exceptions" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "reconciliation_run_id" "uuid" NOT NULL,
    "vendor_id" "uuid",
    "po_id" "uuid",
    "gr_id" "uuid",
    "invoice_id" "uuid",
    "exception_code" "text" NOT NULL,
    "exception_status" "text" DEFAULT 'OPEN'::"text" NOT NULL,
    "gap_amount_local" numeric,
    "expected_amount_local" numeric,
    "actual_amount_local" numeric,
    "suggested_action" "text",
    "client_comment" "text",
    "detected_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "p2p_core"."p2p_reconciliation_exceptions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."relational_integrity_exceptions" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "reconciliation_run_id" "uuid" NOT NULL,
    "vendor_id" "uuid",
    "relation_level" "text" NOT NULL,
    "source_document_type" "text" NOT NULL,
    "source_document_docnum" "text" NOT NULL,
    "current_reference_value" "text",
    "proposed_reference_value" "text",
    "exception_code" "text" NOT NULL,
    "exception_status" "text" DEFAULT 'OPEN'::"text" NOT NULL,
    "allowed_action" "text",
    "client_comment" "text",
    "detected_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "p2p_core"."relational_integrity_exceptions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."sii_invoice_headers" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "vendor_id" "uuid",
    "folio" bigint NOT NULL,
    "dte_type" integer NOT NULL,
    "issue_date" "date" NOT NULL,
    "total_amount" numeric NOT NULL,
    "currency" "text" DEFAULT 'CLP'::"text" NOT NULL,
    "reception_status" "text" DEFAULT 'RECEIVED'::"text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "p2p_core"."sii_invoice_headers" OWNER TO "postgres";


CREATE OR REPLACE VIEW "sem"."vw_document_reception_exceptions" WITH ("security_invoker"='true') AS
 SELECT "dre"."tenant_id",
    "t"."tenant_code",
    "t"."tenant_name",
    "dre"."id" AS "exception_id",
    "dre"."reconciliation_run_id",
    "dre"."exception_code",
    "dre"."exception_status",
    "p2p_core"."derive_exception_priority"('document_reception_exceptions'::"text", "dre"."exception_code", NULL::numeric) AS "exception_priority",
    "dre"."suggested_action",
    "dre"."detected_at",
    "sih"."id" AS "sii_invoice_header_id",
    "sih"."folio" AS "invoice_folio",
    "sih"."dte_type",
    "sih"."issue_date",
    "sih"."total_amount",
    "sih"."currency",
    "sih"."reception_status",
    "v"."vendor_code",
    "v"."vendor_name",
    "v"."vendor_rut"
   FROM ((("p2p_core"."document_reception_exceptions" "dre"
     JOIN "p2p_core"."sii_invoice_headers" "sih" ON ((("sih"."id" = "dre"."sii_invoice_header_id") AND ("sih"."tenant_id" = "dre"."tenant_id"))))
     JOIN "core"."tenants" "t" ON (("t"."id" = "dre"."tenant_id")))
     LEFT JOIN "p2p_core"."vendors" "v" ON ((("v"."id" = "sih"."vendor_id") AND ("v"."tenant_id" = "sih"."tenant_id"))));


ALTER VIEW "sem"."vw_document_reception_exceptions" OWNER TO "postgres";


CREATE OR REPLACE VIEW "sem"."vw_p2p_reconciliation_exceptions" WITH ("security_invoker"='true') AS
 SELECT "pre"."tenant_id",
    "t"."tenant_code",
    "t"."tenant_name",
    "pre"."id" AS "exception_id",
    "pre"."reconciliation_run_id",
    "pre"."exception_code",
    "pre"."exception_status",
    "p2p_core"."derive_exception_priority"('p2p_reconciliation_exceptions'::"text", "pre"."exception_code", "pre"."gap_amount_local") AS "exception_priority",
    "pre"."gap_amount_local",
    "pre"."expected_amount_local",
    "pre"."actual_amount_local",
    "pre"."suggested_action",
    "pre"."client_comment",
    "pre"."detected_at",
    "v"."vendor_code",
    "v"."vendor_name",
    "v"."vendor_rut",
    "po"."doc_num" AS "purchase_order_number",
    "gr"."gr_number" AS "goods_receipt_number",
    "i"."invoice_number"
   FROM ((((("p2p_core"."p2p_reconciliation_exceptions" "pre"
     JOIN "core"."tenants" "t" ON (("t"."id" = "pre"."tenant_id")))
     LEFT JOIN "p2p_core"."vendors" "v" ON ((("v"."id" = "pre"."vendor_id") AND ("v"."tenant_id" = "pre"."tenant_id"))))
     LEFT JOIN "p2p_core"."purchase_orders" "po" ON ((("po"."id" = "pre"."po_id") AND ("po"."tenant_id" = "pre"."tenant_id"))))
     LEFT JOIN "p2p_core"."goods_receipts" "gr" ON ((("gr"."id" = "pre"."gr_id") AND ("gr"."tenant_id" = "pre"."tenant_id"))))
     LEFT JOIN "p2p_core"."invoices" "i" ON ((("i"."id" = "pre"."invoice_id") AND ("i"."tenant_id" = "pre"."tenant_id"))));


ALTER VIEW "sem"."vw_p2p_reconciliation_exceptions" OWNER TO "postgres";


CREATE OR REPLACE VIEW "sem"."vw_relational_integrity_exceptions" WITH ("security_invoker"='true') AS
 SELECT "rie"."tenant_id",
    "t"."tenant_code",
    "t"."tenant_name",
    "rie"."id" AS "exception_id",
    "rie"."reconciliation_run_id",
    "rie"."relation_level",
    "rie"."source_document_type",
    "rie"."source_document_docnum",
    "rie"."current_reference_value",
    "rie"."proposed_reference_value",
    "rie"."exception_code",
    "rie"."exception_status",
    "p2p_core"."derive_exception_priority"('relational_integrity_exceptions'::"text", "rie"."exception_code", NULL::numeric) AS "exception_priority",
    "rie"."allowed_action",
    "rie"."client_comment",
    "rie"."detected_at",
    "v"."vendor_code",
    "v"."vendor_name",
    "v"."vendor_rut"
   FROM (("p2p_core"."relational_integrity_exceptions" "rie"
     JOIN "core"."tenants" "t" ON (("t"."id" = "rie"."tenant_id")))
     LEFT JOIN "p2p_core"."vendors" "v" ON ((("v"."id" = "rie"."vendor_id") AND ("v"."tenant_id" = "rie"."tenant_id"))));


ALTER VIEW "sem"."vw_relational_integrity_exceptions" OWNER TO "postgres";


CREATE OR REPLACE VIEW "sem"."vw_open_exceptions_daily" WITH ("security_invoker"='true') AS
 SELECT "tenant_id",
    "tenant_code",
    "tenant_name",
    "exception_family",
    "exception_id",
    "reconciliation_run_id",
    "exception_code",
    "exception_status",
    "exception_priority",
    "detected_at",
    "reference_doc_1",
    "reference_doc_2",
    "vendor_code",
    "vendor_name",
    "suggested_action",
    "gap_amount_local"
   FROM ( SELECT "d"."tenant_id",
            "d"."tenant_code",
            "d"."tenant_name",
            'DOCUMENT'::"text" AS "exception_family",
            "d"."exception_id",
            "d"."reconciliation_run_id",
            "d"."exception_code",
            "d"."exception_status",
            "d"."exception_priority",
            "d"."detected_at",
            ("d"."invoice_folio")::"text" AS "reference_doc_1",
            NULL::"text" AS "reference_doc_2",
            "d"."vendor_code",
            "d"."vendor_name",
            "d"."suggested_action",
            NULL::numeric AS "gap_amount_local"
           FROM "sem"."vw_document_reception_exceptions" "d"
        UNION ALL
         SELECT "r"."tenant_id",
            "r"."tenant_code",
            "r"."tenant_name",
            'RELATIONAL'::"text" AS "exception_family",
            "r"."exception_id",
            "r"."reconciliation_run_id",
            "r"."exception_code",
            "r"."exception_status",
            "r"."exception_priority",
            "r"."detected_at",
            "r"."source_document_docnum" AS "reference_doc_1",
            "r"."current_reference_value" AS "reference_doc_2",
            "r"."vendor_code",
            "r"."vendor_name",
            "r"."allowed_action" AS "suggested_action",
            NULL::numeric AS "gap_amount_local"
           FROM "sem"."vw_relational_integrity_exceptions" "r"
        UNION ALL
         SELECT "p"."tenant_id",
            "p"."tenant_code",
            "p"."tenant_name",
            'RECONCILIATION'::"text" AS "exception_family",
            "p"."exception_id",
            "p"."reconciliation_run_id",
            "p"."exception_code",
            "p"."exception_status",
            "p"."exception_priority",
            "p"."detected_at",
            ("p"."purchase_order_number")::"text" AS "reference_doc_1",
            ("p"."invoice_number")::"text" AS "reference_doc_2",
            "p"."vendor_code",
            "p"."vendor_name",
            "p"."suggested_action",
            "p"."gap_amount_local"
           FROM "sem"."vw_p2p_reconciliation_exceptions" "p") "x"
  WHERE ("exception_status" = ANY (ARRAY['OPEN'::"text", 'EXPORTED'::"text", 'IN_CORRECTION'::"text", 'SUBMITTED'::"text", 'VALIDATED'::"text"]));


ALTER VIEW "sem"."vw_open_exceptions_daily" OWNER TO "postgres";


CREATE OR REPLACE VIEW "governance"."vw_exception_export_feed" WITH ("security_invoker"='true') AS
 SELECT "tenant_id",
    ("date_trunc"('day'::"text", "detected_at"))::"date" AS "run_date",
    "exception_family",
    "exception_id",
    "exception_code",
    "exception_status",
    "exception_priority",
    "governance"."hash_supplier_hmac"("vendor_name", "vendor_code") AS "supplier_hash",
    "reference_doc_1",
    "reference_doc_2",
    "suggested_action",
    "gap_amount_local"
   FROM "sem"."vw_open_exceptions_daily" "e";


ALTER VIEW "governance"."vw_exception_export_feed" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_audit"."audit_logs" (
    "log_id" bigint NOT NULL,
    "table_name" character varying(100),
    "record_id" "uuid",
    "action" character varying(10),
    "old_data" "jsonb",
    "new_data" "jsonb",
    "changed_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE "p2p_audit"."audit_logs" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "p2p_audit"."audit_logs_log_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "p2p_audit"."audit_logs_log_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "p2p_audit"."audit_logs_log_id_seq" OWNED BY "p2p_audit"."audit_logs"."log_id";



CREATE TABLE IF NOT EXISTS "p2p_core"."correction_upload_rows" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "upload_batch_id" "uuid" NOT NULL,
    "row_number" integer NOT NULL,
    "exception_table" "text" NOT NULL,
    "exception_id" "uuid" NOT NULL,
    "correction_action" "text" NOT NULL,
    "proposed_reference_value" "text",
    "client_reference" "text",
    "client_comment" "text",
    "row_hash" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "p2p_core"."correction_upload_rows" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."match_rule_versions" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "rule_id" "uuid" NOT NULL,
    "tolerance_pct" numeric(5,4),
    "is_active" boolean DEFAULT true,
    "published_by" character varying(100),
    "created_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "p2p_core"."match_rule_versions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."match_rules" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid",
    "rule_name" character varying(100) NOT NULL,
    "rule_type" character varying(50),
    "description" "text",
    "created_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "p2p_core"."match_rules" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."ref_case_statuses" (
    "status_code" "text" NOT NULL,
    "stage_group" "text" NOT NULL,
    "sort_order" integer NOT NULL,
    "is_terminal" boolean DEFAULT false NOT NULL,
    "is_active" boolean DEFAULT true NOT NULL,
    "description" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "ck_ref_case_statuses_sort_order" CHECK (("sort_order" >= 0)),
    CONSTRAINT "ck_ref_case_statuses_stage_group" CHECK (("stage_group" = ANY (ARRAY['OPEN'::"text", 'WORKING'::"text", 'VALIDATION'::"text", 'WRITEBACK'::"text", 'CLOSED'::"text"])))
);


ALTER TABLE "p2p_core"."ref_case_statuses" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."ref_correction_actions" (
    "action_code" "text" NOT NULL,
    "target_exception_table" "text",
    "writes_to_sap" boolean DEFAULT false NOT NULL,
    "target_document_type" "text",
    "is_active" boolean DEFAULT true NOT NULL,
    "description" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "ck_ref_correction_actions_target_document_type" CHECK ((("target_document_type" IS NULL) OR ("target_document_type" = ANY (ARRAY['GOODS_RECEIPT'::"text", 'INVOICE'::"text", 'SUPPLIER_NOTIFICATION'::"text"])))),
    CONSTRAINT "ck_ref_correction_actions_target_exception_table" CHECK ((("target_exception_table" IS NULL) OR ("target_exception_table" = ANY (ARRAY['document_reception_exceptions'::"text", 'relational_integrity_exceptions'::"text", 'p2p_reconciliation_exceptions'::"text"]))))
);


ALTER TABLE "p2p_core"."ref_correction_actions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."ref_exception_codes" (
    "exception_table" "text" NOT NULL,
    "exception_code" "text" NOT NULL,
    "category" "text" NOT NULL,
    "severity" "text" NOT NULL,
    "default_action" "text",
    "description" "text" NOT NULL,
    "is_active" boolean DEFAULT true NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "ck_ref_exception_codes_category" CHECK (("category" = ANY (ARRAY['DOCUMENT'::"text", 'RELATIONAL'::"text", 'RECONCILIATION'::"text"]))),
    CONSTRAINT "ck_ref_exception_codes_severity" CHECK (("severity" = ANY (ARRAY['LOW'::"text", 'MEDIUM'::"text", 'HIGH'::"text", 'CRITICAL'::"text"]))),
    CONSTRAINT "ck_ref_exception_codes_table" CHECK (("exception_table" = ANY (ARRAY['document_reception_exceptions'::"text", 'relational_integrity_exceptions'::"text", 'p2p_reconciliation_exceptions'::"text"])))
);


ALTER TABLE "p2p_core"."ref_exception_codes" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."sap_writeback_queue" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "correction_row_id" "uuid" NOT NULL,
    "writeback_action" "text" NOT NULL,
    "target_document_type" "text" NOT NULL,
    "target_document_doc_entry" "text",
    "target_document_doc_num" "text",
    "target_reference_field" "text",
    "target_reference_value" "text",
    "queue_status" "text" DEFAULT 'READY_TO_PUSH'::"text" NOT NULL,
    "attempt_count" integer DEFAULT 0 NOT NULL,
    "last_attempt_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "p2p_core"."sap_writeback_queue" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "p2p_core"."sii_invoice_lines" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "issue_date" "date",
    "line_number" integer,
    "item_name" "text",
    "item_description" "text",
    "qty" numeric,
    "unit_price" numeric,
    "line_amount" numeric,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "header_id" "uuid"
);


ALTER TABLE "p2p_core"."sii_invoice_lines" OWNER TO "postgres";


CREATE OR REPLACE VIEW "p2p_core"."vw_secure_vendors" AS
 SELECT "id",
    "tenant_id",
    "vendor_code",
    "p2p_core"."fn_mask_name"("vendor_name") AS "vendor_name_masked",
    "p2p_core"."fn_mask_rut"("vendor_rut") AS "vendor_rut_masked",
    "industry_sector",
    "country_code",
    "is_active",
    "created_at"
   FROM "p2p_core"."vendors";


ALTER VIEW "p2p_core"."vw_secure_vendors" OWNER TO "postgres";


CREATE OR REPLACE VIEW "sem"."dim_case_status" AS
 SELECT "status_code",
    "description" AS "status_name",
    "stage_group" AS "status_group",
        CASE
            WHEN "is_terminal" THEN false
            ELSE true
        END AS "is_open_status",
    "is_terminal" AS "is_terminal_status"
   FROM "p2p_core"."ref_case_statuses" "s";


ALTER VIEW "sem"."dim_case_status" OWNER TO "postgres";


COMMENT ON VIEW "sem"."dim_case_status" IS 'Estados operativos homologados para backlog, bandeja y SLA.';



CREATE TABLE IF NOT EXISTS "sem"."dim_date" (
    "date_key" "date" NOT NULL,
    "day_name" "text" NOT NULL,
    "week_start_date" "date" NOT NULL,
    "month_key" "text" NOT NULL,
    "month_name" "text" NOT NULL,
    "quarter_key" "text" NOT NULL,
    "year_num" integer NOT NULL
);


ALTER TABLE "sem"."dim_date" OWNER TO "postgres";


COMMENT ON TABLE "sem"."dim_date" IS 'Calendario analítico estándar para la capa semántica P2P.';



CREATE OR REPLACE VIEW "sem"."dim_exception_type" AS
 SELECT "exception_code",
    "description" AS "exception_name",
    "category" AS "exception_family",
    "severity" AS "severity_default",
    true AS "requires_human_review"
   FROM "p2p_core"."ref_exception_codes" "e";


ALTER VIEW "sem"."dim_exception_type" OWNER TO "postgres";


COMMENT ON VIEW "sem"."dim_exception_type" IS 'Catálogo semántico de causales de excepción.';



CREATE OR REPLACE VIEW "sem"."dim_rule" AS
 SELECT "rv"."id" AS "rule_version_id",
    "r"."id" AS "rule_id",
    "r"."rule_name",
    "r"."rule_type",
    "rv"."tolerance_pct",
    "rv"."is_active"
   FROM ("p2p_core"."match_rule_versions" "rv"
     JOIN "p2p_core"."match_rules" "r" ON (("r"."id" = "rv"."rule_id")));


ALTER VIEW "sem"."dim_rule" OWNER TO "postgres";


COMMENT ON VIEW "sem"."dim_rule" IS 'Reglas y versiones del motor de conciliación para explicabilidad.';



CREATE OR REPLACE VIEW "sem"."dim_tenant" AS
 SELECT "id" AS "tenant_id",
    "tenant_name",
    COALESCE("is_active", true) AS "is_active"
   FROM "core"."tenants" "t";


ALTER VIEW "sem"."dim_tenant" OWNER TO "postgres";


COMMENT ON VIEW "sem"."dim_tenant" IS 'Dimensión de empresa / tenant.';



CREATE OR REPLACE VIEW "sem"."dim_vendor" AS
 SELECT "id" AS "vendor_id",
    "tenant_id",
    "vendor_code",
    "vendor_name",
    "vendor_rut" AS "tax_id",
    "country_code",
    NULL::"text" AS "payment_terms",
    false AS "is_preferred"
   FROM "p2p_core"."vendors" "v";


ALTER VIEW "sem"."dim_vendor" OWNER TO "postgres";


COMMENT ON VIEW "sem"."dim_vendor" IS 'Dimensión de proveedor.';



CREATE OR REPLACE VIEW "sem"."vw_pending_writeback_rows" WITH ("security_invoker"='true') AS
 SELECT "q"."tenant_id",
    "q"."id" AS "queue_id",
    "q"."correction_row_id",
    "q"."writeback_action",
    "q"."target_document_type",
    "q"."target_document_doc_entry",
    "q"."target_document_doc_num",
    "q"."target_reference_field",
    "q"."target_reference_value",
    "q"."queue_status",
    "q"."attempt_count",
    "q"."last_attempt_at",
    "r"."exception_table",
    "r"."exception_id",
    "r"."correction_action",
    "r"."proposed_reference_value",
    "r"."client_comment"
   FROM ("p2p_core"."sap_writeback_queue" "q"
     JOIN "p2p_core"."correction_upload_rows" "r" ON ((("r"."id" = "q"."correction_row_id") AND ("r"."tenant_id" = "q"."tenant_id"))))
  WHERE ("q"."queue_status" = ANY (ARRAY['READY_TO_PUSH'::"text", 'SAP_REJECTED'::"text", 'REPROCESS_PENDING'::"text"]));


ALTER VIEW "sem"."vw_pending_writeback_rows" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "staging"."raw_dte_inbound" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "file_name" "text",
    "payload_xml" "jsonb",
    "status" "text" DEFAULT 'pending'::"text",
    "error_log" "text",
    "created_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "raw_dte_inbound_status_check" CHECK (("status" = ANY (ARRAY['pending'::"text", 'processed'::"text", 'error'::"text"])))
);


ALTER TABLE "staging"."raw_dte_inbound" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "staging"."raw_oc_inbound" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid" NOT NULL,
    "upload_batch_id" "uuid",
    "payload" "jsonb",
    "status" "text" DEFAULT 'pending'::"text",
    "error_log" "text",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "raw_oc_inbound_status_check" CHECK (("status" = ANY (ARRAY['pending'::"text", 'processed'::"text", 'error'::"text"])))
);


ALTER TABLE "staging"."raw_oc_inbound" OWNER TO "postgres";


ALTER TABLE ONLY "p2p_audit"."audit_logs" ALTER COLUMN "log_id" SET DEFAULT "nextval"('"p2p_audit"."audit_logs_log_id_seq"'::"regclass");



ALTER TABLE ONLY "core"."exchange_rates"
    ADD CONSTRAINT "exchange_rates_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "core"."tenants"
    ADD CONSTRAINT "tenants_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "core"."exchange_rates"
    ADD CONSTRAINT "unique_rate_per_day" UNIQUE ("rate_date", "currency_code");



ALTER TABLE ONLY "core"."tenants"
    ADD CONSTRAINT "uq_tenants_tenant_code" UNIQUE ("tenant_code");



ALTER TABLE ONLY "core"."user_tenants"
    ADD CONSTRAINT "uq_user_tenants_user_tenant" UNIQUE ("user_id", "tenant_id");



ALTER TABLE ONLY "core"."user_tenants"
    ADD CONSTRAINT "user_tenants_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_audit"."audit_logs"
    ADD CONSTRAINT "audit_logs_pkey" PRIMARY KEY ("log_id");



ALTER TABLE ONLY "p2p_core"."correction_upload_rows"
    ADD CONSTRAINT "correction_upload_rows_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_core"."document_reception_exceptions"
    ADD CONSTRAINT "document_reception_exceptions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_core"."goods_receipts"
    ADD CONSTRAINT "goods_receipts_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_core"."invoices"
    ADD CONSTRAINT "invoices_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_core"."match_rule_versions"
    ADD CONSTRAINT "match_rule_versions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_core"."match_rules"
    ADD CONSTRAINT "match_rules_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_core"."p2p_reconciliation_exceptions"
    ADD CONSTRAINT "p2p_reconciliation_exceptions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_core"."purchase_orders"
    ADD CONSTRAINT "purchase_orders_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_core"."ref_case_statuses"
    ADD CONSTRAINT "ref_case_statuses_pkey" PRIMARY KEY ("status_code");



ALTER TABLE ONLY "p2p_core"."ref_correction_actions"
    ADD CONSTRAINT "ref_correction_actions_pkey" PRIMARY KEY ("action_code");



ALTER TABLE ONLY "p2p_core"."ref_exception_codes"
    ADD CONSTRAINT "ref_exception_codes_pkey" PRIMARY KEY ("exception_table", "exception_code");



ALTER TABLE ONLY "p2p_core"."relational_integrity_exceptions"
    ADD CONSTRAINT "relational_integrity_exceptions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_core"."sap_writeback_queue"
    ADD CONSTRAINT "sap_writeback_queue_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_core"."sii_invoice_headers"
    ADD CONSTRAINT "sii_invoice_headers_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_core"."sii_invoice_lines"
    ADD CONSTRAINT "sii_invoice_lines_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "p2p_core"."goods_receipts"
    ADD CONSTRAINT "uq_goods_receipts_tenant_gr_number" UNIQUE ("tenant_id", "gr_number");



ALTER TABLE ONLY "p2p_core"."goods_receipts"
    ADD CONSTRAINT "uq_goods_receipts_tenant_id_id" UNIQUE ("tenant_id", "id");



ALTER TABLE ONLY "p2p_core"."goods_receipts"
    ADD CONSTRAINT "uq_goods_receipts_tenant_sap_doc_entry" UNIQUE ("tenant_id", "sap_doc_entry");



ALTER TABLE ONLY "p2p_core"."invoices"
    ADD CONSTRAINT "uq_invoices_tenant_id_id" UNIQUE ("tenant_id", "id");



ALTER TABLE ONLY "p2p_core"."invoices"
    ADD CONSTRAINT "uq_invoices_tenant_invoice_number" UNIQUE ("tenant_id", "invoice_number");



ALTER TABLE ONLY "p2p_core"."invoices"
    ADD CONSTRAINT "uq_invoices_tenant_sap_doc_entry" UNIQUE ("tenant_id", "sap_doc_entry");



ALTER TABLE ONLY "p2p_core"."purchase_orders"
    ADD CONSTRAINT "uq_purchase_orders_tenant_doc_num" UNIQUE ("tenant_id", "doc_num");



ALTER TABLE ONLY "p2p_core"."purchase_orders"
    ADD CONSTRAINT "uq_purchase_orders_tenant_id_id" UNIQUE ("tenant_id", "id");



ALTER TABLE ONLY "p2p_core"."purchase_orders"
    ADD CONSTRAINT "uq_purchase_orders_tenant_sap_doc_entry" UNIQUE ("tenant_id", "sap_doc_entry");



ALTER TABLE ONLY "p2p_core"."sii_invoice_headers"
    ADD CONSTRAINT "uq_sii_invoice_headers_tenant_vendor_dte_folio" UNIQUE ("tenant_id", "vendor_id", "dte_type", "folio");



ALTER TABLE ONLY "p2p_core"."sii_invoice_lines"
    ADD CONSTRAINT "uq_sii_invoice_lines_header_line" UNIQUE ("header_id", "line_number");



ALTER TABLE ONLY "p2p_core"."vendors"
    ADD CONSTRAINT "uq_vendors_tenant_id_unique" UNIQUE ("tenant_id", "id");



ALTER TABLE ONLY "p2p_core"."vendors"
    ADD CONSTRAINT "uq_vendors_tenant_vendor_code" UNIQUE ("tenant_id", "vendor_code");



ALTER TABLE ONLY "p2p_core"."vendors"
    ADD CONSTRAINT "vendors_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "sem"."dim_date"
    ADD CONSTRAINT "dim_date_pkey" PRIMARY KEY ("date_key");



ALTER TABLE ONLY "staging"."raw_dte_inbound"
    ADD CONSTRAINT "raw_dte_inbound_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "staging"."raw_oc_inbound"
    ADD CONSTRAINT "raw_oc_inbound_pkey" PRIMARY KEY ("id");



CREATE INDEX "idx_exchange_rates_lookup" ON "core"."exchange_rates" USING "btree" ("rate_date", "currency_code");



CREATE INDEX "idx_user_tenants_tenant_id" ON "core"."user_tenants" USING "btree" ("tenant_id");



CREATE INDEX "idx_user_tenants_user_id" ON "core"."user_tenants" USING "btree" ("user_id");



CREATE UNIQUE INDEX "uq_user_tenants_default_per_user" ON "core"."user_tenants" USING "btree" ("user_id") WHERE ("is_default" = true);



CREATE INDEX "idx_goods_receipts_po_id" ON "p2p_core"."goods_receipts" USING "btree" ("tenant_id", "po_id");



CREATE INDEX "idx_goods_receipts_tenant_id" ON "p2p_core"."goods_receipts" USING "btree" ("tenant_id");



CREATE INDEX "idx_goods_receipts_vendor_id" ON "p2p_core"."goods_receipts" USING "btree" ("tenant_id", "vendor_id");



CREATE INDEX "idx_invoices_gr_id" ON "p2p_core"."invoices" USING "btree" ("tenant_id", "gr_id");



CREATE INDEX "idx_invoices_tenant_id" ON "p2p_core"."invoices" USING "btree" ("tenant_id");



CREATE INDEX "idx_invoices_vendor_id" ON "p2p_core"."invoices" USING "btree" ("tenant_id", "vendor_id");



CREATE INDEX "idx_purchase_orders_tenant_id" ON "p2p_core"."purchase_orders" USING "btree" ("tenant_id");



CREATE INDEX "idx_purchase_orders_vendor_id" ON "p2p_core"."purchase_orders" USING "btree" ("tenant_id", "vendor_id");



CREATE INDEX "idx_vendors_tenant_id" ON "p2p_core"."vendors" USING "btree" ("tenant_id");



CREATE UNIQUE INDEX "uq_vendors_tenant_vendor_rut" ON "p2p_core"."vendors" USING "btree" ("tenant_id", "vendor_rut") WHERE ("vendor_rut" IS NOT NULL);



CREATE OR REPLACE TRIGGER "trg_tenants_set_updated_at" BEFORE UPDATE ON "core"."tenants" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_user_tenants_set_updated_at" BEFORE UPDATE ON "core"."user_tenants" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_audit_purchase_orders" AFTER INSERT OR DELETE OR UPDATE ON "p2p_core"."purchase_orders" FOR EACH ROW EXECUTE FUNCTION "p2p_audit"."log_changes"();



CREATE OR REPLACE TRIGGER "trg_audit_sii_invoice_headers" AFTER INSERT OR DELETE OR UPDATE ON "p2p_core"."sii_invoice_headers" FOR EACH ROW EXECUTE FUNCTION "p2p_audit"."log_changes"();



CREATE OR REPLACE TRIGGER "trg_audit_vendors" AFTER INSERT OR DELETE OR UPDATE ON "p2p_core"."vendors" FOR EACH ROW EXECUTE FUNCTION "p2p_audit"."log_changes"();



CREATE OR REPLACE TRIGGER "trg_correction_upload_rows_set_updated_at" BEFORE UPDATE ON "p2p_core"."correction_upload_rows" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_document_reception_exceptions_set_updated_at" BEFORE UPDATE ON "p2p_core"."document_reception_exceptions" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_goods_receipts_set_updated_at" BEFORE UPDATE ON "p2p_core"."goods_receipts" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_grn_audit" BEFORE UPDATE ON "p2p_core"."goods_receipts" FOR EACH ROW EXECUTE FUNCTION "core"."fn_update_audit_timestamps"();



CREATE OR REPLACE TRIGGER "trg_invoices_audit" BEFORE UPDATE ON "p2p_core"."invoices" FOR EACH ROW EXECUTE FUNCTION "core"."fn_update_audit_timestamps"();



CREATE OR REPLACE TRIGGER "trg_invoices_set_updated_at" BEFORE UPDATE ON "p2p_core"."invoices" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_p2p_reconciliation_exceptions_set_updated_at" BEFORE UPDATE ON "p2p_core"."p2p_reconciliation_exceptions" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_po_audit" BEFORE UPDATE ON "p2p_core"."purchase_orders" FOR EACH ROW EXECUTE FUNCTION "core"."fn_update_audit_timestamps"();



CREATE OR REPLACE TRIGGER "trg_purchase_orders_set_updated_at" BEFORE UPDATE ON "p2p_core"."purchase_orders" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_ref_case_statuses_set_updated_at" BEFORE UPDATE ON "p2p_core"."ref_case_statuses" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_ref_correction_actions_set_updated_at" BEFORE UPDATE ON "p2p_core"."ref_correction_actions" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_ref_exception_codes_set_updated_at" BEFORE UPDATE ON "p2p_core"."ref_exception_codes" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_relational_integrity_exceptions_set_updated_at" BEFORE UPDATE ON "p2p_core"."relational_integrity_exceptions" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_sap_writeback_queue_set_updated_at" BEFORE UPDATE ON "p2p_core"."sap_writeback_queue" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_sii_invoice_headers_set_updated_at" BEFORE UPDATE ON "p2p_core"."sii_invoice_headers" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_standardize_vendor_rut" BEFORE INSERT OR UPDATE OF "vendor_rut" ON "p2p_core"."vendors" FOR EACH ROW EXECUTE FUNCTION "p2p_core"."fn_standardize_rut"();



CREATE OR REPLACE TRIGGER "trg_vendors_set_updated_at" BEFORE UPDATE ON "p2p_core"."vendors" FOR EACH ROW EXECUTE FUNCTION "core"."set_updated_at"();



ALTER TABLE ONLY "core"."user_tenants"
    ADD CONSTRAINT "fk_user_tenants_vendor" FOREIGN KEY ("vendor_id") REFERENCES "p2p_core"."vendors"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "core"."tenants"
    ADD CONSTRAINT "tenants_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "auth"."users"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "core"."user_tenants"
    ADD CONSTRAINT "user_tenants_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "auth"."users"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "core"."user_tenants"
    ADD CONSTRAINT "user_tenants_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "core"."tenants"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "core"."user_tenants"
    ADD CONSTRAINT "user_tenants_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "p2p_core"."goods_receipts"
    ADD CONSTRAINT "fk_goods_receipts_po" FOREIGN KEY ("tenant_id", "po_id") REFERENCES "p2p_core"."purchase_orders"("tenant_id", "id") ON DELETE RESTRICT;



ALTER TABLE ONLY "p2p_core"."goods_receipts"
    ADD CONSTRAINT "fk_goods_receipts_vendor" FOREIGN KEY ("tenant_id", "vendor_id") REFERENCES "p2p_core"."vendors"("tenant_id", "id") ON DELETE RESTRICT;



ALTER TABLE ONLY "p2p_core"."invoices"
    ADD CONSTRAINT "fk_invoices_goods_receipt" FOREIGN KEY ("tenant_id", "gr_id") REFERENCES "p2p_core"."goods_receipts"("tenant_id", "id") ON DELETE RESTRICT;



ALTER TABLE ONLY "p2p_core"."invoices"
    ADD CONSTRAINT "fk_invoices_vendor" FOREIGN KEY ("tenant_id", "vendor_id") REFERENCES "p2p_core"."vendors"("tenant_id", "id") ON DELETE RESTRICT;



ALTER TABLE ONLY "p2p_core"."purchase_orders"
    ADD CONSTRAINT "fk_purchase_orders_vendor" FOREIGN KEY ("tenant_id", "vendor_id") REFERENCES "p2p_core"."vendors"("tenant_id", "id") ON DELETE RESTRICT;



ALTER TABLE ONLY "p2p_core"."sii_invoice_lines"
    ADD CONSTRAINT "fk_sii_invoice_lines_header" FOREIGN KEY ("header_id") REFERENCES "p2p_core"."sii_invoice_headers"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "p2p_core"."goods_receipts"
    ADD CONSTRAINT "goods_receipts_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "auth"."users"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "p2p_core"."goods_receipts"
    ADD CONSTRAINT "goods_receipts_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "core"."tenants"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "p2p_core"."invoices"
    ADD CONSTRAINT "invoices_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "auth"."users"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "p2p_core"."invoices"
    ADD CONSTRAINT "invoices_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "core"."tenants"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "p2p_core"."match_rule_versions"
    ADD CONSTRAINT "match_rule_versions_rule_id_fkey" FOREIGN KEY ("rule_id") REFERENCES "p2p_core"."match_rules"("id");



ALTER TABLE ONLY "p2p_core"."match_rules"
    ADD CONSTRAINT "match_rules_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "core"."tenants"("id");



ALTER TABLE ONLY "p2p_core"."purchase_orders"
    ADD CONSTRAINT "purchase_orders_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "auth"."users"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "p2p_core"."purchase_orders"
    ADD CONSTRAINT "purchase_orders_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "core"."tenants"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "p2p_core"."vendors"
    ADD CONSTRAINT "vendors_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "auth"."users"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "p2p_core"."vendors"
    ADD CONSTRAINT "vendors_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "core"."tenants"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "staging"."raw_dte_inbound"
    ADD CONSTRAINT "raw_dte_inbound_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "core"."tenants"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "staging"."raw_oc_inbound"
    ADD CONSTRAINT "raw_oc_inbound_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "core"."tenants"("id") ON DELETE CASCADE;



ALTER TABLE "core"."tenants" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "tenants_select_same_tenant" ON "core"."tenants" FOR SELECT TO "authenticated" USING ("core"."can_access_tenant"("id"));



CREATE POLICY "tenants_update_same_tenant" ON "core"."tenants" FOR UPDATE TO "authenticated" USING ("core"."can_access_tenant"("id")) WITH CHECK ("core"."can_access_tenant"("id"));



ALTER TABLE "core"."user_tenants" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "user_tenants_insert_own_active_tenant" ON "core"."user_tenants" FOR INSERT TO "authenticated" WITH CHECK ((("user_id" = "auth"."uid"()) AND ("tenant_id" = "core"."current_tenant_id"()) AND "core"."can_access_tenant"("tenant_id")));



CREATE POLICY "user_tenants_select_own_memberships" ON "core"."user_tenants" FOR SELECT TO "authenticated" USING ((("user_id" = "auth"."uid"()) AND "core"."can_access_tenant"("tenant_id")));



CREATE POLICY "user_tenants_update_own_active_tenant" ON "core"."user_tenants" FOR UPDATE TO "authenticated" USING ((("user_id" = "auth"."uid"()) AND "core"."can_access_tenant"("tenant_id"))) WITH CHECK ((("user_id" = "auth"."uid"()) AND ("tenant_id" = "core"."current_tenant_id"()) AND "core"."can_access_tenant"("tenant_id")));



ALTER TABLE "p2p_core"."goods_receipts" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "goods_receipts_insert_same_tenant" ON "p2p_core"."goods_receipts" FOR INSERT TO "authenticated" WITH CHECK ((("tenant_id" = "core"."current_tenant_id"()) AND "core"."can_access_tenant"("tenant_id")));



CREATE POLICY "goods_receipts_select_same_tenant" ON "p2p_core"."goods_receipts" FOR SELECT TO "authenticated" USING ("core"."can_access_tenant"("tenant_id"));



CREATE POLICY "goods_receipts_update_same_tenant" ON "p2p_core"."goods_receipts" FOR UPDATE TO "authenticated" USING ("core"."can_access_tenant"("tenant_id")) WITH CHECK ((("tenant_id" = "core"."current_tenant_id"()) AND "core"."can_access_tenant"("tenant_id")));



ALTER TABLE "p2p_core"."invoices" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "invoices_insert_same_tenant" ON "p2p_core"."invoices" FOR INSERT TO "authenticated" WITH CHECK ((("tenant_id" = "core"."current_tenant_id"()) AND "core"."can_access_tenant"("tenant_id")));



CREATE POLICY "invoices_select_strict_profiles" ON "p2p_core"."invoices" FOR SELECT TO "authenticated" USING (("core"."can_access_tenant"("tenant_id") AND ((("core"."get_user_context"("tenant_id"))."v_role" = ANY (ARRAY['cognity_admin'::"text", 'cognity_analyst'::"text", 'client_admin'::"text", 'client_user'::"text"])) OR ((("core"."get_user_context"("tenant_id"))."v_role" = 'vendor_user'::"text") AND ("vendor_id" = ("core"."get_user_context"("tenant_id"))."v_vendor_id")))));



CREATE POLICY "invoices_update_same_tenant" ON "p2p_core"."invoices" FOR UPDATE TO "authenticated" USING ("core"."can_access_tenant"("tenant_id")) WITH CHECK ((("tenant_id" = "core"."current_tenant_id"()) AND "core"."can_access_tenant"("tenant_id")));



ALTER TABLE "p2p_core"."purchase_orders" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "purchase_orders_insert_same_tenant" ON "p2p_core"."purchase_orders" FOR INSERT TO "authenticated" WITH CHECK ((("tenant_id" = "core"."current_tenant_id"()) AND "core"."can_access_tenant"("tenant_id")));



CREATE POLICY "purchase_orders_select_same_tenant" ON "p2p_core"."purchase_orders" FOR SELECT TO "authenticated" USING ("core"."can_access_tenant"("tenant_id"));



CREATE POLICY "purchase_orders_update_same_tenant" ON "p2p_core"."purchase_orders" FOR UPDATE TO "authenticated" USING ("core"."can_access_tenant"("tenant_id")) WITH CHECK ((("tenant_id" = "core"."current_tenant_id"()) AND "core"."can_access_tenant"("tenant_id")));



ALTER TABLE "p2p_core"."ref_case_statuses" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "ref_case_statuses_read_all_authenticated" ON "p2p_core"."ref_case_statuses" FOR SELECT TO "authenticated" USING (true);



ALTER TABLE "p2p_core"."ref_correction_actions" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "ref_correction_actions_read_all_authenticated" ON "p2p_core"."ref_correction_actions" FOR SELECT TO "authenticated" USING (true);



ALTER TABLE "p2p_core"."ref_exception_codes" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "ref_exception_codes_read_all_authenticated" ON "p2p_core"."ref_exception_codes" FOR SELECT TO "authenticated" USING (true);



ALTER TABLE "p2p_core"."vendors" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "vendors_insert_same_tenant" ON "p2p_core"."vendors" FOR INSERT TO "authenticated" WITH CHECK ((("tenant_id" = "core"."current_tenant_id"()) AND "core"."can_access_tenant"("tenant_id")));



CREATE POLICY "vendors_select_same_tenant" ON "p2p_core"."vendors" FOR SELECT TO "authenticated" USING ("core"."can_access_tenant"("tenant_id"));



CREATE POLICY "vendors_update_same_tenant" ON "p2p_core"."vendors" FOR UPDATE TO "authenticated" USING ("core"."can_access_tenant"("tenant_id")) WITH CHECK ((("tenant_id" = "core"."current_tenant_id"()) AND "core"."can_access_tenant"("tenant_id")));



ALTER TABLE "staging"."raw_dte_inbound" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "staging"."raw_oc_inbound" ENABLE ROW LEVEL SECURITY;




ALTER PUBLICATION "supabase_realtime" OWNER TO "postgres";


GRANT USAGE ON SCHEMA "core" TO "authenticated";
GRANT USAGE ON SCHEMA "core" TO "service_role";
GRANT USAGE ON SCHEMA "core" TO "anon";






GRANT USAGE ON SCHEMA "governance" TO "authenticated";
GRANT USAGE ON SCHEMA "governance" TO "service_role";



GRANT USAGE ON SCHEMA "p2p_core" TO "authenticated";
GRANT USAGE ON SCHEMA "p2p_core" TO "service_role";
GRANT USAGE ON SCHEMA "p2p_core" TO "anon";



GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";



GRANT USAGE ON SCHEMA "sem" TO "authenticated";
GRANT USAGE ON SCHEMA "sem" TO "service_role";



GRANT ALL ON FUNCTION "core"."can_access_tenant"("p_tenant_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "core"."can_access_tenant"("p_tenant_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "core"."can_access_tenant"("p_tenant_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "core"."current_anonymization_secret"() TO "anon";
GRANT ALL ON FUNCTION "core"."current_anonymization_secret"() TO "authenticated";
GRANT ALL ON FUNCTION "core"."current_anonymization_secret"() TO "service_role";



GRANT ALL ON FUNCTION "core"."current_tenant_id"() TO "anon";
GRANT ALL ON FUNCTION "core"."current_tenant_id"() TO "authenticated";
GRANT ALL ON FUNCTION "core"."current_tenant_id"() TO "service_role";



GRANT ALL ON FUNCTION "core"."current_user_id"() TO "anon";
GRANT ALL ON FUNCTION "core"."current_user_id"() TO "authenticated";
GRANT ALL ON FUNCTION "core"."current_user_id"() TO "service_role";



GRANT ALL ON FUNCTION "core"."fn_update_audit_timestamps"() TO "anon";
GRANT ALL ON FUNCTION "core"."fn_update_audit_timestamps"() TO "authenticated";
GRANT ALL ON FUNCTION "core"."fn_update_audit_timestamps"() TO "service_role";



GRANT ALL ON FUNCTION "core"."get_user_context"("p_tenant_id" "uuid", OUT "v_role" "text", OUT "v_vendor_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "core"."get_user_context"("p_tenant_id" "uuid", OUT "v_role" "text", OUT "v_vendor_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "core"."get_user_context"("p_tenant_id" "uuid", OUT "v_role" "text", OUT "v_vendor_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "core"."is_tenant_member"("p_tenant_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "core"."is_tenant_member"("p_tenant_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "core"."is_tenant_member"("p_tenant_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "core"."set_updated_at"() TO "anon";
GRANT ALL ON FUNCTION "core"."set_updated_at"() TO "authenticated";
GRANT ALL ON FUNCTION "core"."set_updated_at"() TO "service_role";











































































































































































GRANT ALL ON FUNCTION "p2p_core"."allowed_correction_actions"("p_exception_table" "text", "p_exception_code" "text") TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."allowed_correction_actions"("p_exception_table" "text", "p_exception_code" "text") TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."allowed_correction_actions"("p_exception_table" "text", "p_exception_code" "text") TO "service_role";



GRANT ALL ON FUNCTION "p2p_core"."can_edit_case_status"("p_status" "text") TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."can_edit_case_status"("p_status" "text") TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."can_edit_case_status"("p_status" "text") TO "service_role";



GRANT ALL ON FUNCTION "p2p_core"."derive_exception_priority"("p_exception_table" "text", "p_exception_code" "text", "p_gap_amount_local" numeric) TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."derive_exception_priority"("p_exception_table" "text", "p_exception_code" "text", "p_gap_amount_local" numeric) TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."derive_exception_priority"("p_exception_table" "text", "p_exception_code" "text", "p_gap_amount_local" numeric) TO "service_role";



GRANT ALL ON FUNCTION "p2p_core"."fn_anonymize_old_vendors"("retention_years" integer) TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."fn_anonymize_old_vendors"("retention_years" integer) TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."fn_anonymize_old_vendors"("retention_years" integer) TO "service_role";



GRANT ALL ON FUNCTION "p2p_core"."fn_mask_name"("name_input" "text") TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."fn_mask_name"("name_input" "text") TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."fn_mask_name"("name_input" "text") TO "service_role";



GRANT ALL ON FUNCTION "p2p_core"."fn_mask_rut"("rut_input" "text") TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."fn_mask_rut"("rut_input" "text") TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."fn_mask_rut"("rut_input" "text") TO "service_role";



GRANT ALL ON FUNCTION "p2p_core"."fn_standardize_rut"() TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."fn_standardize_rut"() TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."fn_standardize_rut"() TO "service_role";



GRANT ALL ON FUNCTION "p2p_core"."is_valid_correction_action"("p_exception_table" "text", "p_exception_code" "text", "p_action" "text") TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."is_valid_correction_action"("p_exception_table" "text", "p_exception_code" "text", "p_action" "text") TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."is_valid_correction_action"("p_exception_table" "text", "p_exception_code" "text", "p_action" "text") TO "service_role";



GRANT ALL ON FUNCTION "p2p_core"."make_row_hash"("p_exception_table" "text", "p_exception_id" "uuid", "p_target_value" "text", "p_client_reference" "text") TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."make_row_hash"("p_exception_table" "text", "p_exception_id" "uuid", "p_target_value" "text", "p_client_reference" "text") TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."make_row_hash"("p_exception_table" "text", "p_exception_id" "uuid", "p_target_value" "text", "p_client_reference" "text") TO "service_role";



GRANT ALL ON FUNCTION "p2p_core"."normalized_text"("p_value" "text") TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."normalized_text"("p_value" "text") TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."normalized_text"("p_value" "text") TO "service_role";



GRANT ALL ON FUNCTION "p2p_core"."rpc_process_dte"("p_staging_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."rpc_process_dte"("p_staging_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."rpc_process_dte"("p_staging_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "p2p_core"."rpc_process_oc"("p_staging_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."rpc_process_oc"("p_staging_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."rpc_process_oc"("p_staging_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "p2p_core"."validate_correction_upload_row"() TO "anon";
GRANT ALL ON FUNCTION "p2p_core"."validate_correction_upload_row"() TO "authenticated";
GRANT ALL ON FUNCTION "p2p_core"."validate_correction_upload_row"() TO "service_role";



GRANT ALL ON FUNCTION "public"."get_tenant_id"() TO "anon";
GRANT ALL ON FUNCTION "public"."get_tenant_id"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_tenant_id"() TO "service_role";












GRANT ALL ON TABLE "core"."exchange_rates" TO "anon";
GRANT ALL ON TABLE "core"."exchange_rates" TO "authenticated";
GRANT ALL ON TABLE "core"."exchange_rates" TO "service_role";



GRANT ALL ON TABLE "core"."tenants" TO "authenticated";
GRANT ALL ON TABLE "core"."tenants" TO "service_role";
GRANT ALL ON TABLE "core"."tenants" TO "anon";



GRANT ALL ON TABLE "core"."user_tenants" TO "authenticated";
GRANT ALL ON TABLE "core"."user_tenants" TO "service_role";
GRANT ALL ON TABLE "core"."user_tenants" TO "anon";















GRANT ALL ON TABLE "p2p_core"."goods_receipts" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."goods_receipts" TO "service_role";
GRANT ALL ON TABLE "p2p_core"."goods_receipts" TO "anon";



GRANT ALL ON TABLE "p2p_core"."invoices" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."invoices" TO "service_role";
GRANT ALL ON TABLE "p2p_core"."invoices" TO "anon";



GRANT ALL ON TABLE "p2p_core"."purchase_orders" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."purchase_orders" TO "service_role";
GRANT ALL ON TABLE "p2p_core"."purchase_orders" TO "anon";



GRANT ALL ON TABLE "p2p_core"."vendors" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."vendors" TO "service_role";
GRANT ALL ON TABLE "p2p_core"."vendors" TO "anon";



GRANT SELECT ON TABLE "sem"."vw_blind_money_p2p" TO "authenticated";
GRANT SELECT ON TABLE "sem"."vw_blind_money_p2p" TO "service_role";



GRANT SELECT ON TABLE "governance"."vw_anonymized_audit" TO "authenticated";
GRANT SELECT ON TABLE "governance"."vw_anonymized_audit" TO "service_role";



GRANT ALL ON TABLE "p2p_core"."document_reception_exceptions" TO "anon";
GRANT ALL ON TABLE "p2p_core"."document_reception_exceptions" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."document_reception_exceptions" TO "service_role";



GRANT ALL ON TABLE "p2p_core"."p2p_reconciliation_exceptions" TO "anon";
GRANT ALL ON TABLE "p2p_core"."p2p_reconciliation_exceptions" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."p2p_reconciliation_exceptions" TO "service_role";



GRANT ALL ON TABLE "p2p_core"."relational_integrity_exceptions" TO "anon";
GRANT ALL ON TABLE "p2p_core"."relational_integrity_exceptions" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."relational_integrity_exceptions" TO "service_role";



GRANT ALL ON TABLE "p2p_core"."sii_invoice_headers" TO "anon";
GRANT ALL ON TABLE "p2p_core"."sii_invoice_headers" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."sii_invoice_headers" TO "service_role";



GRANT SELECT ON TABLE "sem"."vw_document_reception_exceptions" TO "authenticated";
GRANT SELECT ON TABLE "sem"."vw_document_reception_exceptions" TO "service_role";



GRANT SELECT ON TABLE "sem"."vw_p2p_reconciliation_exceptions" TO "authenticated";
GRANT SELECT ON TABLE "sem"."vw_p2p_reconciliation_exceptions" TO "service_role";



GRANT SELECT ON TABLE "sem"."vw_relational_integrity_exceptions" TO "authenticated";
GRANT SELECT ON TABLE "sem"."vw_relational_integrity_exceptions" TO "service_role";



GRANT SELECT ON TABLE "sem"."vw_open_exceptions_daily" TO "authenticated";
GRANT SELECT ON TABLE "sem"."vw_open_exceptions_daily" TO "service_role";



GRANT SELECT ON TABLE "governance"."vw_exception_export_feed" TO "authenticated";
GRANT SELECT ON TABLE "governance"."vw_exception_export_feed" TO "service_role";



GRANT ALL ON TABLE "p2p_core"."correction_upload_rows" TO "anon";
GRANT ALL ON TABLE "p2p_core"."correction_upload_rows" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."correction_upload_rows" TO "service_role";



GRANT ALL ON TABLE "p2p_core"."match_rule_versions" TO "anon";
GRANT ALL ON TABLE "p2p_core"."match_rule_versions" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."match_rule_versions" TO "service_role";



GRANT ALL ON TABLE "p2p_core"."match_rules" TO "anon";
GRANT ALL ON TABLE "p2p_core"."match_rules" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."match_rules" TO "service_role";



GRANT ALL ON TABLE "p2p_core"."ref_case_statuses" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."ref_case_statuses" TO "service_role";
GRANT ALL ON TABLE "p2p_core"."ref_case_statuses" TO "anon";



GRANT ALL ON TABLE "p2p_core"."ref_correction_actions" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."ref_correction_actions" TO "service_role";
GRANT ALL ON TABLE "p2p_core"."ref_correction_actions" TO "anon";



GRANT ALL ON TABLE "p2p_core"."ref_exception_codes" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."ref_exception_codes" TO "service_role";
GRANT ALL ON TABLE "p2p_core"."ref_exception_codes" TO "anon";



GRANT ALL ON TABLE "p2p_core"."sap_writeback_queue" TO "anon";
GRANT ALL ON TABLE "p2p_core"."sap_writeback_queue" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."sap_writeback_queue" TO "service_role";



GRANT ALL ON TABLE "p2p_core"."sii_invoice_lines" TO "anon";
GRANT ALL ON TABLE "p2p_core"."sii_invoice_lines" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."sii_invoice_lines" TO "service_role";



GRANT ALL ON TABLE "p2p_core"."vw_secure_vendors" TO "anon";
GRANT ALL ON TABLE "p2p_core"."vw_secure_vendors" TO "authenticated";
GRANT ALL ON TABLE "p2p_core"."vw_secure_vendors" TO "service_role";



GRANT SELECT ON TABLE "sem"."vw_pending_writeback_rows" TO "authenticated";
GRANT SELECT ON TABLE "sem"."vw_pending_writeback_rows" TO "service_role";









ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "core" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "core" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "core" GRANT ALL ON SEQUENCES TO "service_role";



ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "core" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "core" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "core" GRANT ALL ON FUNCTIONS TO "service_role";



ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "core" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "core" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "core" GRANT ALL ON TABLES TO "service_role";



ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "p2p_core" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "p2p_core" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "p2p_core" GRANT ALL ON SEQUENCES TO "service_role";



ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "p2p_core" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "p2p_core" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "p2p_core" GRANT ALL ON FUNCTIONS TO "service_role";



ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "p2p_core" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "p2p_core" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "p2p_core" GRANT ALL ON TABLES TO "service_role";



ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "service_role";































drop extension if exists "pg_net";


