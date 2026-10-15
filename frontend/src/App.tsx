import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  AlertTriangle, 
  Play, 
  RefreshCw, 
  Download, 
  ChevronDown, 
  ChevronUp, 
  FileText, 
  Moon, 
  Sun, 
  Settings as SettingsIcon,
  Check,
  AlertCircle
} from 'lucide-react';
import { UploadCard } from './components/UploadCard';
import type { JobStatus } from './types';

const STAGES = [
  'Reading Excel...',
  'Extracting Records...',
  'Looking up GSTIN...',
  'Validating...',
  'Mapping Data...',
  'Generating Excel...',
  'Conversion Complete'
];

interface JobSettings {
  enableLookup: boolean;
  enableValidation: boolean;
  enableErrorSheet: boolean;
  saveConversionLog: boolean;
}

// Full backend origin (e.g. https://api.yourdomain.com) for independent deploys.
// Falls back to same-origin (BASE_URL) when unset, e.g. local dev via Vite proxy
// or when the backend still serves the built frontend itself.
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? import.meta.env.BASE_URL).replace(/\/$/, '');


function App() {
  // Theme State
  const [darkMode, setDarkMode] = useState(() => {
    const saved = localStorage.getItem('theme');
    return saved === 'dark' || (!saved && window.matchMedia('(prefers-color-scheme: dark)').matches);
  });

  // Files State
  const [file2A, setFile2A] = useState<File | null>(null);
  const [file2B, setFile2B] = useState<File | null>(null);
  
  // Settings Collapsible State
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<JobSettings>({
    enableLookup: true,
    enableValidation: true,
    enableErrorSheet: true,
    saveConversionLog: true
  });

  // Job status
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [logs, setLogs] = useState<string[]>([]);

  // Polling management
  const pollingIntervalRef = useRef<number | null>(null);
  const logsCountRef = useRef(0);

  // Dark Theme Sync
  useEffect(() => {
    if (darkMode) {
      document.body.classList.add('dark');
      localStorage.setItem('theme', 'dark');
    } else {
      document.body.classList.remove('dark');
      localStorage.setItem('theme', 'light');
    }
  }, [darkMode]);

  // Clean up polling on unmount
  useEffect(() => {
    return () => stopPolling();
  }, []);

  const startPolling = (jobId: string) => {
    stopPolling();
    logsCountRef.current = 0;
    setLogs([]);
    
    pollingIntervalRef.current = window.setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/status?job_id=${jobId}&last_log_index=${logsCountRef.current}`);
        if (!res.ok) {
          stopPolling();
          return;
        }

        const statusData: JobStatus = await res.json();
        setJobStatus(statusData);

        // Update logs stream
        if (statusData.logs && statusData.logs.length > 0) {
          setLogs(prev => [...prev, ...statusData.logs]);
          logsCountRef.current += statusData.logs.length;
        }

        if (statusData.status === 'completed' || statusData.status === 'failed') {
          stopPolling();
        }
      } catch (e) {
        console.error('Error polling status:', e);
        stopPolling();
      }
    }, 850);
  };

  const stopPolling = () => {
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
  };

  const handleStartConversion = async () => {
    const has2A = !!file2A;
    const has2B = !!file2B;
    
    if (!has2A && !has2B) return;

    const formData = new FormData();
    formData.append('enableLookup', String(settings.enableLookup));
    formData.append('enableValidation', String(settings.enableValidation));
    formData.append('enableErrorSheet', String(settings.enableErrorSheet));
    formData.append('saveConversionLog', String(settings.saveConversionLog));
    formData.append('overwriteExisting', 'true');
    formData.append('outputFileName', 'Purchase_Import_Output.xlsx');

    let endpoint = `${API_BASE}/api/convert/2a`;
    let type: '2a' | '2b' | 'both' = '2a';

    if (has2A && !has2B) {
      formData.append('inputFile', file2A);
      endpoint = `${API_BASE}/api/convert/2a`;
      type = '2a';
    } else if (!has2A && has2B) {
      formData.append('inputFile', file2B);
      endpoint = `${API_BASE}/api/convert/2b`;
      type = '2b';
    } else if (has2A && has2B) {
      formData.append('file_2a', file2A);
      formData.append('file_2b', file2B);
      endpoint = `${API_BASE}/api/convert/both`;
      type = 'both';
    }

    // Set initial temporary queued state
    setJobStatus({
      type,
      status: 'queued',
      step: 'Initializing...',
      progress: 5,
      stats_2a: createEmptyStats(),
      stats_2b: createEmptyStats(),
      logs: [],
      errors_2a: [],
      errors_2b: [],
      output_file_2a: null,
      output_file_2b: null,
      log_file: null,
      error_report_file_2a: null,
      error_report_file_2b: null,
      elapsed_time: 0.0
    });
    setLogs([]);

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || 'Failed to start conversion.');
      }

      const data = await res.json();
      setCurrentJobId(data.job_id);
      startPolling(data.job_id);
    } catch (e: any) {
      alert(e.message || 'An error occurred while launching conversion.');
      setJobStatus(null);
    }
  };

  const createEmptyStats = () => ({
    invoices_processed: 0,
    lookups_completed: 0,
    cache_hits: 0,
    cache_misses: 0,
    valid_invoices: 0,
    invalid_invoices: 0,
    total_unique_gstins: 0,
    validation_errors_count: 0,
    lookup_failures_count: 0
  });

  const handleDownload = (jobId: string, fileType: 'output_2a' | 'output_2b' | 'log' | 'error_2a' | 'error_2b') => {
    window.open(`${API_BASE}/api/download/${jobId}/${fileType}`, '_blank');
  };

  const isConverting = jobStatus?.status === 'running' || jobStatus?.status === 'queued';

  // Map backend logs to our beautiful stages
  const getCurrentStage = (): string => {
    if (!jobStatus) return STAGES[0];
    if (jobStatus.status === 'failed') return 'Conversion Halted';
    if (jobStatus.status === 'completed') return 'Conversion Complete';
    
    for (let i = logs.length - 1; i >= 0; i--) {
      const log = logs[i].toLowerCase();
      if (log.includes('generating') || log.includes('exporting') || log.includes('export_to_excel') || log.includes('workbook')) {
        return 'Generating Excel...';
      }
      if (log.includes('mapping') || log.includes('map_records')) {
        return 'Mapping Data...';
      }
      if (log.includes('validating') || log.includes('rules') || log.includes('validate_records')) {
        return 'Validating...';
      }
      if (log.includes('resolving') || log.includes('lookup') || log.includes('lookup_gstin')) {
        return 'Looking up GSTIN...';
      }
      if (log.includes('parsed') || log.includes('extracting')) {
        return 'Extracting Records...';
      }
      if (log.includes('starting parser') || log.includes('read') || log.includes('parse_gstr')) {
        return 'Reading Excel...';
      }
    }
    
    // Fallback on status steps
    const stepLower = jobStatus.step.toLowerCase();
    if (stepLower.includes('2a') && !stepLower.includes('2b')) return 'Reading Excel...';
    
    return 'Reading Excel...';
  };

  const activeStage = getCurrentStage();
  const activeStageIndex = STAGES.indexOf(activeStage);

  // Cumulative Stats
  const totalRawRecords = (jobStatus?.stats_2a?.invoices_processed || 0) + (jobStatus?.stats_2b?.invoices_processed || 0);
  const totalProcessedRecords = (jobStatus?.stats_2a?.valid_invoices || 0) + (jobStatus?.stats_2b?.valid_invoices || 0);
  const totalValidationErrors = (jobStatus?.stats_2a?.validation_errors_count || 0) + (jobStatus?.stats_2b?.validation_errors_count || 0);

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-[#070b13] text-slate-800 dark:text-slate-100 transition-colors duration-300 font-sans py-10 px-4">
      <div className="max-w-4xl mx-auto space-y-6">
        
        {/* Header Section */}
        <header className="flex flex-col sm:flex-row justify-between items-start sm:items-center bg-white/70 dark:bg-slate-900/60 backdrop-blur-md border border-slate-200/50 dark:border-slate-800/50 rounded-2xl p-6 shadow-sm">
          <div className="text-left">
            <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-blue-600 via-indigo-600 to-purple-600 dark:from-blue-400 dark:via-indigo-400 dark:to-purple-400">
              GST 2A / 2B to Tally Converter
            </h1>
            <p className="text-xs md:text-sm text-slate-505 dark:text-slate-400 mt-1.5 max-w-xl font-medium">
              Transform your GSTR-2A and GSTR-2B purchase returns spreadsheets into clean, Tally-compatible purchase import sheets in seconds.
            </p>
          </div>
          <button
            onClick={() => setDarkMode(!darkMode)}
            className="mt-4 sm:mt-0 p-2.5 rounded-xl bg-slate-100 dark:bg-slate-850 hover:bg-slate-200 dark:hover:bg-slate-800 text-slate-650 dark:text-slate-300 border border-slate-200/50 dark:border-slate-800/40 transition-all duration-200"
            title="Toggle Theme"
          >
            {darkMode ? <Sun className="w-5 h-5 text-amber-500" /> : <Moon className="w-5 h-5 text-indigo-500" />}
          </button>
        </header>

        {/* Upload Cards Layout */}
        <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* GSTR-2A File Card */}
          <div className="bg-white dark:bg-slate-900 border border-slate-200/60 dark:border-slate-800/60 rounded-2xl p-5 shadow-sm space-y-3 flex flex-col justify-between">
            <div className="text-left">
              <span className="text-[10px] font-bold tracking-wider uppercase bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-400 px-2.5 py-0.5 rounded-full">
                GSTR-2A File
              </span>
              <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-300 mt-2">
                Upload GSTR-2A Spreadsheet
              </h2>
            </div>
            <UploadCard
              title="Drag & drop GSTR-2A spreadsheet"
              subtitle="Supports .xlsx, .xls formats"
              accept=".xlsx,.xls"
              file={file2A}
              onChange={setFile2A}
              iconType="excel"
              disabled={isConverting}
            />
          </div>

          {/* GSTR-2B File Card */}
          <div className="bg-white dark:bg-slate-900 border border-slate-200/60 dark:border-slate-800/60 rounded-2xl p-5 shadow-sm space-y-3 flex flex-col justify-between">
            <div className="text-left">
              <span className="text-[10px] font-bold tracking-wider uppercase bg-indigo-50 dark:bg-indigo-950/40 text-indigo-650 dark:text-indigo-400 px-2.5 py-0.5 rounded-full">
                GSTR-2B File
              </span>
              <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-300 mt-2">
                Upload GSTR-2B Spreadsheet
              </h2>
            </div>
            <UploadCard
              title="Drag & drop GSTR-2B spreadsheet"
              subtitle="Supports .xlsx, .xls formats"
              accept=".xlsx,.xls"
              file={file2B}
              onChange={setFile2B}
              iconType="excel"
              disabled={isConverting}
            />
          </div>
        </section>

        {/* Collapsible Settings Panel */}
        <section className="bg-white dark:bg-slate-900 border border-slate-200/60 dark:border-slate-800/60 rounded-2xl shadow-sm overflow-hidden">
          <button
            onClick={() => setIsSettingsOpen(!isSettingsOpen)}
            className="w-full flex items-center justify-between p-5 hover:bg-slate-50/50 dark:hover:bg-slate-850/20 transition-colors"
          >
            <div className="flex items-center gap-2.5 text-left">
              <SettingsIcon className="w-5 h-5 text-indigo-500" />
              <div>
                <span className="font-semibold text-slate-800 dark:text-slate-250 text-sm">
                  Advanced Configuration Settings
                </span>
                <p className="text-[11px] text-slate-400 dark:text-slate-500">
                  Adjust strict checks, lookup rules, and logging options
                </p>
              </div>
            </div>
            {isSettingsOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>

          <AnimatePresence initial={false}>
            {isSettingsOpen && (
              <motion.div
                initial={{ height: 0 }}
                animate={{ height: 'auto' }}
                exit={{ height: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden border-t border-slate-100 dark:border-slate-800 bg-slate-50/20 dark:bg-slate-950/10"
              >
                <div className="p-5 grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* GSTIN Lookup */}
                  <label className="flex items-center justify-between p-3.5 bg-white dark:bg-slate-900/60 rounded-xl border border-slate-150 dark:border-slate-800/60 cursor-pointer select-none hover:bg-slate-50 dark:hover:bg-slate-850/40 transition-colors">
                    <div className="text-left pr-4">
                      <span className="text-xs font-semibold text-slate-700 dark:text-slate-300 block">
                        Enable GSTIN Lookup
                      </span>
                      <span className="text-[10px] text-slate-400 dark:text-slate-500">
                        Query database cache / GSTzen for legal supplier names
                      </span>
                    </div>
                    <div className="relative inline-flex items-center">
                      <input
                        type="checkbox"
                        checked={settings.enableLookup}
                        onChange={() => setSettings(s => ({ ...s, enableLookup: !s.enableLookup }))}
                        disabled={isConverting}
                        className="sr-only peer"
                      />
                      <div className="w-9 h-5 bg-slate-200 dark:bg-slate-750 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all dark:border-slate-600 peer-checked:bg-blue-600"></div>
                    </div>
                  </label>

                  {/* Validation */}
                  <label className="flex items-center justify-between p-3.5 bg-white dark:bg-slate-900/60 rounded-xl border border-slate-150 dark:border-slate-800/60 cursor-pointer select-none hover:bg-slate-50 dark:hover:bg-slate-850/40 transition-colors">
                    <div className="text-left pr-4">
                      <span className="text-xs font-semibold text-slate-700 dark:text-slate-300 block">
                        Enable Validation
                      </span>
                      <span className="text-[10px] text-slate-400 dark:text-slate-500">
                        Strictly validate supplier records and invoice structures
                      </span>
                    </div>
                    <div className="relative inline-flex items-center">
                      <input
                        type="checkbox"
                        checked={settings.enableValidation}
                        onChange={() => setSettings(s => ({ ...s, enableValidation: !s.enableValidation }))}
                        disabled={isConverting}
                        className="sr-only peer"
                      />
                      <div className="w-9 h-5 bg-slate-200 dark:bg-slate-750 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all dark:border-slate-600 peer-checked:bg-blue-600"></div>
                    </div>
                  </label>

                  {/* Generate Error Sheet */}
                  <label className="flex items-center justify-between p-3.5 bg-white dark:bg-slate-900/60 rounded-xl border border-slate-150 dark:border-slate-800/60 cursor-pointer select-none hover:bg-slate-50 dark:hover:bg-slate-850/40 transition-colors">
                    <div className="text-left pr-4">
                      <span className="text-xs font-semibold text-slate-700 dark:text-slate-300 block">
                        Generate Error Sheet
                      </span>
                      <span className="text-[10px] text-slate-400 dark:text-slate-500">
                        Append an extra tab for failed invoices in the workbook
                      </span>
                    </div>
                    <div className="relative inline-flex items-center">
                      <input
                        type="checkbox"
                        checked={settings.enableErrorSheet}
                        onChange={() => setSettings(s => ({ ...s, enableErrorSheet: !s.enableErrorSheet }))}
                        disabled={isConverting}
                        className="sr-only peer"
                      />
                      <div className="w-9 h-5 bg-slate-200 dark:bg-slate-750 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all dark:border-slate-600 peer-checked:bg-blue-600"></div>
                    </div>
                  </label>

                  {/* Save Conversion Log */}
                  <label className="flex items-center justify-between p-3.5 bg-white dark:bg-slate-900/60 rounded-xl border border-slate-150 dark:border-slate-800/60 cursor-pointer select-none hover:bg-slate-50 dark:hover:bg-slate-850/40 transition-colors">
                    <div className="text-left pr-4">
                      <span className="text-xs font-semibold text-slate-700 dark:text-slate-300 block">
                        Save Conversion Log
                      </span>
                      <span className="text-[10px] text-slate-400 dark:text-slate-500">
                        Write parsing and runtime steps to a downloadable log file
                      </span>
                    </div>
                    <div className="relative inline-flex items-center">
                      <input
                        type="checkbox"
                        checked={settings.saveConversionLog}
                        onChange={() => setSettings(s => ({ ...s, saveConversionLog: !s.saveConversionLog }))}
                        disabled={isConverting}
                        className="sr-only peer"
                      />
                      <div className="w-9 h-5 bg-slate-200 dark:bg-slate-750 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all dark:border-slate-600 peer-checked:bg-blue-600"></div>
                    </div>
                  </label>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </section>

        {/* Convert Button Container */}
        <section>
          <button
            onClick={handleStartConversion}
            disabled={(!file2A && !file2B) || isConverting}
            className={`w-full py-4 rounded-2xl flex items-center justify-center gap-2.5 font-bold text-sm text-white shadow-lg transition-all duration-205 border border-transparent ${
              (!file2A && !file2B) || isConverting
                ? 'bg-slate-300 dark:bg-slate-800 text-slate-500 dark:text-slate-600 cursor-not-allowed shadow-none'
                : 'bg-gradient-to-r from-blue-600 via-indigo-600 to-purple-600 hover:from-blue-700 hover:via-indigo-700 hover:to-purple-700 shadow-indigo-500/10 dark:shadow-indigo-950/20 active:scale-[0.99] cursor-pointer'
            }`}
          >
            {isConverting ? (
              <>
                <RefreshCw className="w-4 h-4 animate-spin" />
                <span>Processing Files...</span>
              </>
            ) : (
              <>
                <Play className="w-4 h-4" />
                <span>Convert Files</span>
              </>
            )}
          </button>
        </section>

        {/* Progress Section */}
        <AnimatePresence>
          {jobStatus && (
            <motion.section
              initial={{ opacity: 0, y: 15 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 15 }}
              className="bg-white dark:bg-slate-900 border border-slate-200/60 dark:border-slate-800/60 rounded-2xl p-6 shadow-sm space-y-6 text-left"
            >
              <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-3">
                <div>
                  <h3 className="font-bold text-slate-800 dark:text-slate-200 text-sm">
                    Conversion Process Status
                  </h3>
                  <span className="text-[10px] text-slate-400 dark:text-slate-500">
                    ID: {currentJobId || 'Queued...'}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-xs font-semibold text-slate-655 dark:text-slate-400">
                  <span>Progress:</span>
                  <span className="font-bold text-indigo-600 dark:text-indigo-400">{jobStatus.progress}%</span>
                </div>
              </div>

              {/* Progress Stage Tracker (Visual checklist) */}
              <div className="relative pl-6 space-y-4 border-l border-slate-200 dark:border-slate-800 ml-3">
                {STAGES.map((stageName, index) => {
                  const isDone = index < activeStageIndex;
                  const isCurrent = index === activeStageIndex && jobStatus.status !== 'failed';
                  const isFailed = index === activeStageIndex && jobStatus.status === 'failed';
                  
                  let iconColor = 'bg-slate-200 text-slate-405 dark:bg-slate-800 dark:text-slate-600';
                  let iconElement = <span className="w-1.5 h-1.5 rounded-full bg-current" />;
                  
                  if (isDone) {
                    iconColor = 'bg-emerald-500 text-white';
                    iconElement = <Check className="w-3 h-3 stroke-[3]" />;
                  } else if (isCurrent) {
                    iconColor = 'bg-blue-600 text-white animate-pulse';
                    iconElement = <RefreshCw className="w-3 h-3 animate-spin" />;
                  } else if (isFailed) {
                    iconColor = 'bg-rose-500 text-white';
                    iconElement = <AlertTriangle className="w-3.5 h-3.5" />;
                  }

                  return (
                    <div key={index} className="relative flex items-center gap-3">
                      <div className={`absolute -left-[35px] w-5 h-5 rounded-full flex items-center justify-center ${iconColor} border-2 border-white dark:border-slate-900 transition-colors`}>
                        {iconElement}
                      </div>
                      <span className={`text-xs font-semibold transition-colors ${
                        isCurrent ? 'text-blue-600 dark:text-blue-400 font-bold' :
                        isFailed ? 'text-rose-505 font-bold' :
                        isDone ? 'text-slate-800 dark:text-slate-200' :
                        'text-slate-400 dark:text-slate-600'
                      }`}>
                        {stageName}
                      </span>
                    </div>
                  );
                })}
              </div>

              {/* Animated Progress Bar */}
              <div className="w-full bg-slate-105 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
                <div 
                  className={`h-2 rounded-full transition-all duration-300 ${
                    jobStatus.status === 'failed' ? 'bg-rose-500' :
                    jobStatus.status === 'completed' ? 'bg-emerald-500' :
                    'bg-indigo-605'
                  }`}
                  style={{ width: `${jobStatus.progress}%` }}
                />
              </div>

              {/* Record Processing Stats Indicators */}
              <div className="grid grid-cols-3 gap-4 bg-slate-50 dark:bg-slate-950/40 border border-slate-100 dark:border-slate-850 p-4 rounded-xl">
                <div className="flex flex-col">
                  <span className="text-[10px] text-slate-400 dark:text-slate-500 uppercase tracking-wider font-semibold">
                    Total Records
                  </span>
                  <span className="text-lg font-bold text-slate-800 dark:text-slate-200 mt-0.5">
                    {totalRawRecords}
                  </span>
                </div>
                <div className="flex flex-col border-l border-slate-200 dark:border-slate-800 pl-4">
                  <span className="text-[10px] text-slate-400 dark:text-slate-505 uppercase tracking-wider font-semibold">
                    Processed Records
                  </span>
                  <span className="text-lg font-bold text-emerald-600 dark:text-emerald-400 mt-0.5">
                    {totalProcessedRecords}
                  </span>
                </div>
                <div className="flex flex-col border-l border-slate-200 dark:border-slate-800 pl-4">
                  <span className="text-[10px] text-slate-400 dark:text-slate-505 uppercase tracking-wider font-semibold">
                    Validation Errors
                  </span>
                  <span className={`text-lg font-bold mt-0.5 ${totalValidationErrors > 0 ? 'text-rose-500' : 'text-slate-400'}`}>
                    {totalValidationErrors}
                  </span>
                </div>
              </div>
            </motion.section>
          )}
        </AnimatePresence>

        {/* Results Section */}
        <AnimatePresence>
          {jobStatus && (jobStatus.status === 'completed' || jobStatus.status === 'failed') && (
            <motion.section
              initial={{ opacity: 0, y: 15 }}
              animate={{ opacity: 1, y: 0 }}
              className="space-y-4"
            >
              <h3 className="text-left font-bold text-slate-700 dark:text-slate-350 text-sm pl-1">
                Conversion Results Summary
              </h3>

              {jobStatus.status === 'completed' ? (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* GSTR-2A Result Card */}
                  {jobStatus.output_file_2a && (
                    <article className={`bg-white dark:bg-slate-900 border border-slate-200/60 dark:border-slate-800/60 rounded-2xl p-5 shadow-sm text-left flex flex-col justify-between gap-4 ${!jobStatus.output_file_2b ? 'md:col-span-2' : ''}`}>
                      <div className="space-y-2">
                        <div className="flex justify-between items-center">
                          <span className="text-[9px] font-bold uppercase tracking-wider bg-emerald-50 dark:bg-emerald-950/40 text-emerald-650 dark:text-emerald-400 px-2 py-0.5 rounded-full">
                            GSTR-2A Output
                          </span>
                          <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500">
                            Status: <strong className="text-emerald-500">Completed</strong>
                          </span>
                        </div>
                        <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-200 truncate mt-1" title={file2A?.name}>
                          {file2A?.name || 'GSTR-2A Input File'}
                        </h4>
                        
                        <div className="grid grid-cols-3 gap-2 border-t border-slate-105 dark:border-slate-800/80 pt-3 text-xs">
                          <div>
                            <span className="text-slate-400 dark:text-slate-500 block text-[9px] uppercase font-bold tracking-wider">Total</span>
                            <span className="font-bold text-slate-700 dark:text-slate-300">{jobStatus.stats_2a.invoices_processed}</span>
                          </div>
                          <div>
                            <span className="text-slate-400 dark:text-slate-500 block text-[9px] uppercase font-bold tracking-wider">Success</span>
                            <span className="font-bold text-emerald-600 dark:text-emerald-400">{jobStatus.stats_2a.valid_invoices}</span>
                          </div>
                          <div>
                            <span className="text-slate-400 dark:text-slate-500 block text-[9px] uppercase font-bold tracking-wider">Failed</span>
                            <span className={`font-bold ${jobStatus.stats_2a.validation_errors_count > 0 ? 'text-rose-500' : 'text-slate-400'}`}>{jobStatus.stats_2a.validation_errors_count}</span>
                          </div>
                        </div>
                      </div>

                      <div className="space-y-2 pt-2 border-t border-slate-105 dark:border-slate-800/80">
                        <button
                          onClick={() => handleDownload(currentJobId!, 'output_2a')}
                          className="w-full py-2 bg-emerald-500 hover:bg-emerald-605 text-white font-bold text-xs rounded-xl flex items-center justify-center gap-1.5 transition-all shadow-sm shadow-emerald-500/10 dark:shadow-none cursor-pointer"
                        >
                          <Download className="w-3.5 h-3.5" />
                          Download Output
                        </button>
                        
                        {jobStatus.error_report_file_2a && jobStatus.stats_2a.validation_errors_count > 0 && (
                          <button
                            onClick={() => handleDownload(currentJobId!, 'error_2a')}
                            className="w-full py-2 bg-rose-50 dark:bg-rose-950/20 text-rose-600 dark:text-rose-400 hover:bg-rose-100 dark:hover:bg-rose-950/40 font-bold text-xs rounded-xl flex items-center justify-center gap-1.5 transition-all border border-rose-100 dark:border-rose-900/30 cursor-pointer"
                          >
                            <AlertCircle className="w-3.5 h-3.5" />
                            Download Error Report
                          </button>
                        )}
                      </div>
                    </article>
                  )}

                  {/* GSTR-2B Result Card */}
                  {jobStatus.output_file_2b && (
                    <article className={`bg-white dark:bg-slate-900 border border-slate-200/60 dark:border-slate-800/60 rounded-2xl p-5 shadow-sm text-left flex flex-col justify-between gap-4 ${!jobStatus.output_file_2a ? 'md:col-span-2' : ''}`}>
                      <div className="space-y-2">
                        <div className="flex justify-between items-center">
                          <span className="text-[9px] font-bold uppercase tracking-wider bg-emerald-50 dark:bg-emerald-950/40 text-emerald-650 dark:text-emerald-400 px-2 py-0.5 rounded-full">
                            GSTR-2B Output
                          </span>
                          <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500">
                            Status: <strong className="text-emerald-500">Completed</strong>
                          </span>
                        </div>
                        <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-200 truncate mt-1" title={file2B?.name}>
                          {file2B?.name || 'GSTR-2B Input File'}
                        </h4>
                        
                        <div className="grid grid-cols-3 gap-2 border-t border-slate-105 dark:border-slate-800/80 pt-3 text-xs">
                          <div>
                            <span className="text-slate-400 dark:text-slate-500 block text-[9px] uppercase font-bold tracking-wider">Total</span>
                            <span className="font-bold text-slate-700 dark:text-slate-300">{jobStatus.stats_2b.invoices_processed}</span>
                          </div>
                          <div>
                            <span className="text-slate-400 dark:text-slate-500 block text-[9px] uppercase font-bold tracking-wider">Success</span>
                            <span className="font-bold text-emerald-600 dark:text-emerald-400">{jobStatus.stats_2b.valid_invoices}</span>
                          </div>
                          <div>
                            <span className="text-slate-400 dark:text-slate-500 block text-[9px] uppercase font-bold tracking-wider">Failed</span>
                            <span className={`font-bold ${jobStatus.stats_2b.validation_errors_count > 0 ? 'text-rose-505' : 'text-slate-400'}`}>{jobStatus.stats_2b.validation_errors_count}</span>
                          </div>
                        </div>
                      </div>

                      <div className="space-y-2 pt-2 border-t border-slate-105 dark:border-slate-800/80">
                        <button
                          onClick={() => handleDownload(currentJobId!, 'output_2b')}
                          className="w-full py-2 bg-emerald-500 hover:bg-emerald-605 text-white font-bold text-xs rounded-xl flex items-center justify-center gap-1.5 transition-all shadow-sm shadow-emerald-500/10 dark:shadow-none cursor-pointer"
                        >
                          <Download className="w-3.5 h-3.5" />
                          Download Output
                        </button>
                        
                        {jobStatus.error_report_file_2b && jobStatus.stats_2b.validation_errors_count > 0 && (
                          <button
                            onClick={() => handleDownload(currentJobId!, 'error_2b')}
                            className="w-full py-2 bg-rose-50 dark:bg-rose-950/20 text-rose-600 dark:text-rose-400 hover:bg-rose-100 dark:hover:bg-rose-950/40 font-bold text-xs rounded-xl flex items-center justify-center gap-1.5 transition-all border border-rose-100 dark:border-rose-900/30 cursor-pointer"
                          >
                            <AlertCircle className="w-3.5 h-3.5" />
                            Download Error Report
                          </button>
                        )}
                      </div>
                    </article>
                  )}
                </div>
              ) : (
                /* Failure Result Block */
                <div className="bg-rose-50/50 dark:bg-rose-950/10 border border-rose-200/40 dark:border-rose-900/30 p-5 rounded-2xl flex flex-col sm:flex-row items-center justify-between gap-4 text-left">
                  <div className="space-y-1">
                    <h4 className="font-bold text-slate-800 dark:text-slate-205 flex items-center gap-2 text-sm">
                      <AlertTriangle className="w-5 h-5 text-rose-500" />
                      Conversion Failed
                    </h4>
                    <p className="text-xs text-slate-500 dark:text-slate-400 leading-relaxed max-w-xl">
                      A critical error occurred while executing the converter parsing or validation routines. Check the execution logs for troubleshooting.
                    </p>
                  </div>
                  {jobStatus.log_file && (
                    <button
                      onClick={() => handleDownload(currentJobId!, 'log')}
                      className="flex-shrink-0 w-full sm:w-auto px-4 py-2 bg-rose-500 hover:bg-rose-600 text-white font-bold text-xs rounded-xl flex items-center justify-center gap-1.5 transition-all cursor-pointer"
                    >
                      <FileText className="w-4 h-4" />
                      Download Logs
                    </button>
                  )}
                </div>
              )}
            </motion.section>
          )}
        </AnimatePresence>

      </div>
    </div>
  );
}

export default App;
