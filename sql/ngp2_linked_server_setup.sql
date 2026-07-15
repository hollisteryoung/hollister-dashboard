-- ============================================================
-- NGP2 Linked Server Setup
-- Run ONCE on the local SQL Server instance (requires sysadmin)
-- Creates a linked server named [db_ProcessData] pointing at
-- the external db_ProcessData host so that ngp2_sync.sql and
-- ngp2_sync_preview.sql can resolve 4-part cross-server names.
-- ============================================================

-- ── 1. SET THE REMOTE SERVER ADDRESS ──────────────────────────
--    Replace the value below with the actual hostname or IP.
--    For a named instance use: N'192.168.x.x\INSTANCENAME'
--    For the default instance use: N'192.168.x.x'
-- ──────────────────────────────────────────────────────────────
DECLARE @remote_host NVARCHAR(128) = N'10.62.27.4,1433';

-- ── 2. REMOVE OLD LINKED SERVER IF IT EXISTS ──────────────────
IF EXISTS (SELECT 1 FROM sys.servers WHERE name = N'db_ProcessData')
BEGIN
    EXEC sp_dropserver N'db_ProcessData', 'droplogins';
    PRINT 'Dropped existing linked server db_ProcessData';
END

-- ── 3. CREATE THE LINKED SERVER ───────────────────────────────
--    MSOLEDBSQL is the modern provider (SQL Server 2012+).
--    If you get provider errors, try SQLNCLI11 instead.
EXEC sp_addlinkedserver
    @server     = N'db_ProcessData',
    @srvproduct = N'',
    @provider   = N'MSOLEDBSQL',
    @datasrc    = @remote_host;

PRINT 'Created linked server db_ProcessData -> ' + @remote_host;

-- ── 4. CONFIGURE LOGIN MAPPING ────────────────────────────────
--    SQL Server login — fill in the credentials you use in DBeaver
--    to connect to db_ProcessData on 10.62.27.4:
--
EXEC sp_addlinkedsrvlogin
    @rmtsrvname  = N'db_ProcessData',
    @useself     = N'False',
    @locallogin  = NULL,
    @rmtuser     = N'',     -- TODO: fill in
    @rmtpassword = N'';  -- TODO: fill in

-- ── 5. CONFIGURE OPTIONS ──────────────────────────────────────
EXEC sp_serveroption N'db_ProcessData', 'rpc out',           'true';
EXEC sp_serveroption N'db_ProcessData', 'data access',       'true';
EXEC sp_serveroption N'db_ProcessData', 'collation compatible', 'false';

-- ── 6. VERIFY ─────────────────────────────────────────────────
--    Run this to confirm the linked server can be reached.
--    Should return the SQL Server version string of the remote host.
--
-- SELECT * FROM OPENQUERY([db_ProcessData], 'SELECT @@VERSION');
--
--    Or list databases visible on the remote server:
--
-- EXEC sp_tables_ex N'db_ProcessData';

PRINT '';
PRINT 'Setup complete. Uncomment the OPENQUERY line above to verify connectivity.';
PRINT 'Then run ngp2_sync_preview.sql (connect to local instance) to check pending rows.';
