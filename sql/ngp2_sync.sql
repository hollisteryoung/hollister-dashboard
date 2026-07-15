-- ============================================================
-- NGP2 Weekly Sync  |  db_ProcessData → ngp2_may7n-27n
-- ============================================================
-- HOW TO USE:
--   1. Update @cutoff to the new end-of-week boundary (shift start time 07:07).
--   2. Connect to the LOCAL SQL Server instance in DBeaver (ngp2_may7n-27n host).
--      Do NOT connect to db_ProcessData — the linked server handles that.
--      Requires [ProcessData_Src] linked server (run ngp2_linked_server_setup.sql once).
--   3. Run the whole script. Each table auto-detects its local MAX and inserts
--      only rows that are newer. Re-running is safe — no duplicates possible
--      because the anchor uses strict > (not >=).
--   4. Check the PRINT output in DBeaver's Log/Messages tab for row counts.
--
-- Run ngp2_sync_preview.sql first if you want a dry-run row count.
-- ============================================================

USE [ngp2_may7n-27n];

-- ── UPDATE THIS EACH WEEK ──────────────────────────────────────────────────
DECLARE @cutoff DATETIME = '2026-06-20 07:07:00';
-- ──────────────────────────────────────────────────────────────────────────

DECLARE @from   DATETIME;
DECLARE @n      INT;
DECLARE @total  INT = 0;

PRINT '=== NGP2 Sync started: cutoff = ' + CONVERT(VARCHAR, @cutoff, 120) + ' ===';
PRINT '';

-- ── Core ──────────────────────────────────────────────────────────────────────

-- StatusBlocks uses Start_TS / End_TS (not t_stamp)
SELECT @from = MAX(Start_TS) FROM dbo.tbl_SCP_NGP2_StatusBlocks WHERE Start_TS < @cutoff;
INSERT INTO dbo.tbl_SCP_NGP2_StatusBlocks
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_SCP_NGP2_StatusBlocks
    WHERE Start_TS > @from AND Start_TS < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_SCP_NGP2_StatusBlocks               ' + CAST(@n AS VARCHAR) + ' rows  (from ' + ISNULL(CONVERT(VARCHAR, @from, 120), 'NULL') + ')';

SELECT @from = MAX(Start_TS) FROM dbo.tbl_SCP_NGP2_DowntimeOverlays WHERE Start_TS < @cutoff;
INSERT INTO dbo.tbl_SCP_NGP2_DowntimeOverlays
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_SCP_NGP2_DowntimeOverlays
    WHERE Start_TS > @from AND Start_TS < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_SCP_NGP2_DowntimeOverlays           ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_FOFchanges_WithReasonCode;
INSERT INTO dbo.tbl_TG_NGP2_HH_FOFchanges_WithReasonCode
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_FOFchanges_WithReasonCode
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_FOFchanges_WithReasonCode  ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_FOFchanges_WithReasonCode_ALT;
INSERT INTO dbo.tbl_TG_NGP2_HH_FOFchanges_WithReasonCode_ALT
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_FOFchanges_WithReasonCode_ALT
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_FOFchanges_WithReasonCode_ALT  ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_LiveOEE;
INSERT INTO dbo.tbl_TG_NGP2_HH_LiveOEE
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_LiveOEE
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_LiveOEE                  ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_OmacState;
INSERT INTO dbo.tbl_TG_NGP2_HH_OmacState
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_OmacState
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_OmacState                ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_OutputStats;
INSERT INTO dbo.tbl_TG_NGP2_HH_OutputStats
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_OutputStats
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_OutputStats              ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_PartsPerMinute;
INSERT INTO dbo.tbl_TG_NGP2_HH_PartsPerMinute
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_PartsPerMinute
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_PartsPerMinute           ' + CAST(@n AS VARCHAR) + ' rows';

PRINT '';

-- ── Hydration ─────────────────────────────────────────────────────────────────
PRINT '-- Hydration --';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_AlarmStack;
INSERT INTO dbo.tbl_TG_NGP2_HH_AlarmStack
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_AlarmStack
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_AlarmStack               ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_Buttons_TEMP;
INSERT INTO dbo.tbl_TG_NGP2_HH_Buttons_TEMP
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_Buttons_TEMP
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_Buttons_TEMP             ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_OperatorStopEvents;
INSERT INTO dbo.tbl_TG_NGP2_HH_OperatorStopEvents
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_OperatorStopEvents
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_OperatorStopEvents       ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_Resets_TEMP;
INSERT INTO dbo.tbl_TG_NGP2_HH_Resets_TEMP
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_Resets_TEMP
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_Resets_TEMP              ' + CAST(@n AS VARCHAR) + ' rows';

-- StationRejectCounterSummary is XML — linked server can't stream it directly.
-- Cast to NVARCHAR(MAX) on the remote side via OPENQUERY, cast back to XML on insert.
SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_StationRejectCountSummary;
DECLARE @q2   CHAR(1)       = CHAR(39);
DECLARE @fs   VARCHAR(30)   = ISNULL(CONVERT(VARCHAR(30), @from, 121), '1900-01-01');
DECLARE @cs   VARCHAR(30)   = CONVERT(VARCHAR(30), @cutoff, 120);
DECLARE @xsql NVARCHAR(MAX) =
    N'INSERT INTO dbo.tbl_TG_NGP2_HH_StationRejectCountSummary' +
    N' (tbl_tg_ngp2_hh_stationrejectcountsummary_ndx, StationRejectCounterSummary, t_stamp, quality_code)' +
    N' SELECT tbl_tg_ngp2_hh_stationrejectcountsummary_ndx,' +
    N'        CAST(StationRejectCounterSummary AS XML), t_stamp, quality_code' +
    N' FROM OPENQUERY([db_ProcessData], ' + @q2 +
    N'SELECT tbl_tg_ngp2_hh_stationrejectcountsummary_ndx,' +
    N' CAST(StationRejectCounterSummary AS NVARCHAR(MAX)) AS StationRejectCounterSummary,' +
    N' t_stamp, quality_code' +
    N' FROM db_ProcessData.dbo.tbl_TG_NGP2_HH_StationRejectCountSummary' +
    N' WHERE t_stamp > ' + @q2+@q2 + @fs + @q2+@q2 +
    N' AND t_stamp < '   + @q2+@q2 + @cs + @q2+@q2 + @q2 + N');' +
    N' SET @rows = @@ROWCOUNT';
DECLARE @xml_n INT;
EXEC sp_executesql @xsql, N'@rows INT OUTPUT', @rows = @xml_n OUTPUT;
SET @n = @xml_n; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_StationRejectCountSummary  ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Format;
INSERT INTO dbo.tbl_TG_NGP2_Format
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Format
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Format                      ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_StationsCounters_Hydration;
INSERT INTO dbo.tbl_TG_NGP2_HH_StationsCounters_Hydration
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_StationsCounters_Hydration
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_StationsCounters_Hydration  ' + CAST(@n AS VARCHAR) + ' rows';

PRINT '';

-- ── Foil ──────────────────────────────────────────────────────────────────────
PRINT '-- Foil --';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_StationsCounters_Foil;
INSERT INTO dbo.tbl_TG_NGP2_HH_StationsCounters_Foil
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_StationsCounters_Foil
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_HH_StationsCounters_Foil    ' + CAST(@n AS VARCHAR) + ' rows';

PRINT '';

-- ── Foam ──────────────────────────────────────────────────────────────────────
PRINT '-- Foam --';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_CurrentProduct;
INSERT INTO dbo.tbl_TG_NGP2_Foam_CurrentProduct
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_CurrentProduct
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Foam_CurrentProduct         ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_FoamFunctionState;
INSERT INTO dbo.tbl_TG_NGP2_Foam_FoamFunctionState
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_FoamFunctionState
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Foam_FoamFunctionState      ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_MachineState;
INSERT INTO dbo.tbl_TG_NGP2_Foam_MachineState
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_MachineState
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Foam_MachineState           ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_MessageTextRaw;
INSERT INTO dbo.tbl_TG_NGP2_Foam_MessageTextRaw
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_MessageTextRaw
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Foam_MessageTextRaw         ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_AirInjectFlowRate;
INSERT INTO dbo.tbl_TG_NGP2_Foam_AirInjectFlowRate
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_AirInjectFlowRate
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Foam_AirInjectFlowRate      ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_AirInjectPressure;
INSERT INTO dbo.tbl_TG_NGP2_Foam_AirInjectPressure
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_AirInjectPressure
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Foam_AirInjectPressure      ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_NozzleTimeActual;
INSERT INTO dbo.tbl_TG_NGP2_Foam_NozzleTimeActual
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_NozzleTimeActual
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Foam_NozzleTimeActual       ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_SolutionFlowRate;
INSERT INTO dbo.tbl_TG_NGP2_Foam_SolutionFlowRate
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_SolutionFlowRate
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Foam_SolutionFlowRate       ' + CAST(@n AS VARCHAR) + ' rows';

PRINT '';

-- ── Schubert ──────────────────────────────────────────────────────────────────
PRINT '-- Schubert --';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Schubert_ActiveProduct;
INSERT INTO dbo.tbl_TG_NGP2_Schubert_ActiveProduct
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Schubert_ActiveProduct
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Schubert_ActiveProduct      ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Schubert_AlarmStack;
INSERT INTO dbo.tbl_TG_NGP2_Schubert_AlarmStack
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Schubert_AlarmStack
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Schubert_AlarmStack         ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Schubert_MachineMode;
INSERT INTO dbo.tbl_TG_NGP2_Schubert_MachineMode
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Schubert_MachineMode
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Schubert_MachineMode        ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Schubert_MachineState;
INSERT INTO dbo.tbl_TG_NGP2_Schubert_MachineState
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Schubert_MachineState
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_NGP2_Schubert_MachineState       ' + CAST(@n AS VARCHAR) + ' rows';

PRINT '';
PRINT '=== Sync complete. Total rows inserted: ' + CAST(@total AS VARCHAR) + ' ===';
