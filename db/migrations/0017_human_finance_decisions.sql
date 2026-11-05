-- Fix 4, finance half: approving a cash match and disputing or accepting a deduction are human
-- decisions. Migration 0006 let the engine change those statuses, but no engine code does; the
-- grant only meant the engine could record a decision a person is supposed to make. The engine
-- keeps invoice status (it marks invoices paid when cash is applied).
REVOKE UPDATE (status) ON deduction, cash_application FROM cpgos_engine;

-- A person approving a match posts the cash, so the invoice it pays becomes paid or partly paid,
-- exactly as when the engine posts automatically.
GRANT UPDATE (status) ON invoice TO cpgos_reviewer;
