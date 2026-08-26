import { useEffect, useState, type FormEvent } from "react";
import type { Device, Facility, Institution } from "@optometry-clinic-sync/contracts";
import { ArrowLeft, Building2, Check, Clipboard, Hospital, LogOut, MonitorUp, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import { api } from "./api";

type EnrollmentResult = {
  institution: { id: string; name: string };
  facility: { id: string; name: string };
  device: Device;
  enrollment: { token: string; expiresAt: string };
};

const statusLabels: Record<Device["status"], string> = {
  pending_enrollment: "Awaiting enrollment",
  enrolled: "Enrolled",
  revoked: "Revoked",
};

export function App() {
  const [adminKey, setAdminKey] = useState("");
  const [authenticatedKey, setAuthenticatedKey] = useState("");
  const [institutions, setInstitutions] = useState<Institution[]>([]);
  const [selectedInstitution, setSelectedInstitution] = useState<Institution | null>(null);
  const [facilities, setFacilities] = useState<Facility[]>([]);
  const [selectedFacility, setSelectedFacility] = useState<Facility | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [showCreateInstitution, setShowCreateInstitution] = useState(false);
  const [showCreateFacility, setShowCreateFacility] = useState(false);
  const [showAddDevice, setShowAddDevice] = useState(false);
  const [enrollment, setEnrollment] = useState<EnrollmentResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function loadInstitutions(key = authenticatedKey) {
    const result = await api.listInstitutions(key);
    setInstitutions(result.institutions);
  }

  async function loadFacilities(institutionId: string) {
    const result = await api.listFacilities(authenticatedKey, institutionId);
    setFacilities(result.facilities);
  }

  async function loadDevices(institutionId: string, facilityId: string) {
    const result = await api.listDevices(authenticatedKey, institutionId, facilityId);
    setDevices(result.devices);
  }

  useEffect(() => {
    if (!selectedInstitution || !selectedFacility || !authenticatedKey) return;
    const timer = window.setInterval(() => {
      loadDevices(selectedInstitution.id, selectedFacility.id).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [selectedInstitution, selectedFacility, authenticatedKey]);

  async function login(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await loadInstitutions(adminKey);
      setAuthenticatedKey(adminKey);
      setAdminKey("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to sign in.");
    } finally {
      setBusy(false);
    }
  }

  async function openInstitution(institution: Institution) {
    setBusy(true);
    setError("");
    try {
      await loadFacilities(institution.id);
      setSelectedInstitution(institution);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load institution.");
    } finally {
      setBusy(false);
    }
  }

  async function openFacility(facility: Facility) {
    if (!selectedInstitution) return;
    setBusy(true);
    setError("");
    try {
      await loadDevices(selectedInstitution.id, facility.id);
      setSelectedFacility(facility);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load facility.");
    } finally {
      setBusy(false);
    }
  }

  if (!authenticatedKey) {
    return (
      <main className="login-shell">
        <section className="login-panel">
          <div className="brand-mark"><ShieldCheck size={30} /></div>
          <p className="eyebrow">Clinic Sync Administration</p>
          <h1>Administrator access</h1>
          <p className="lede">Use the development administration key configured on the sync API.</p>
          <form onSubmit={login} className="stack-form">
            <label>Administration key<input type="password" value={adminKey} onChange={(event) => setAdminKey(event.target.value)} autoComplete="off" required minLength={16} /></label>
            {error && <p className="error-message">{error}</p>}
            <button className="primary-button" disabled={busy || adminKey.length < 16}>{busy ? "Checking..." : "Continue"}</button>
          </form>
          <p className="dev-notice">Development authentication. Configure <code>ADMIN_API_KEY</code> on the API.</p>
        </section>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup"><div className="brand-mark small"><ShieldCheck size={22} /></div><span>Clinic Sync</span></div>
        <nav><button className="nav-item active"><Building2 size={18} /> Institutions</button></nav>
        <button className="nav-item logout" onClick={() => { setAuthenticatedKey(""); setSelectedInstitution(null); setSelectedFacility(null); }}><LogOut size={18} /> Sign out</button>
      </aside>
      <main className="workspace">
        {selectedInstitution && selectedFacility ? (
          <FacilityView
            institution={selectedInstitution}
            facility={selectedFacility}
            devices={devices}
            enrollment={enrollment}
            error={error}
            busy={busy}
            showAddDevice={showAddDevice}
            onBack={() => { setSelectedFacility(null); setEnrollment(null); setShowAddDevice(false); setError(""); }}
            onRefresh={() => loadDevices(selectedInstitution.id, selectedFacility.id)}
            onAdd={() => { setShowAddDevice(true); setEnrollment(null); }}
            onCancelAdd={() => setShowAddDevice(false)}
            onCreated={(result) => { setEnrollment(result); setShowAddDevice(false); void loadDevices(selectedInstitution.id, selectedFacility.id); }}
            adminKey={authenticatedKey}
            setBusy={setBusy}
            setError={setError}
          />
        ) : selectedInstitution ? (
          <FacilitiesView
            institution={selectedInstitution}
            facilities={facilities}
            showCreate={showCreateFacility}
            busy={busy}
            error={error}
            onBack={() => { setSelectedInstitution(null); setFacilities([]); setError(""); }}
            onOpen={openFacility}
            onShowCreate={() => setShowCreateFacility(true)}
            onCancel={() => setShowCreateFacility(false)}
            onCreated={(facility) => { setFacilities((current) => [...current, facility].sort((a, b) => a.name.localeCompare(b.name))); setShowCreateFacility(false); }}
            adminKey={authenticatedKey}
            setBusy={setBusy}
            setError={setError}
          />
        ) : (
          <InstitutionsView
            institutions={institutions}
            showCreate={showCreateInstitution}
            busy={busy}
            error={error}
            onOpen={openInstitution}
            onShowCreate={() => setShowCreateInstitution(true)}
            onCancel={() => setShowCreateInstitution(false)}
            onCreated={(institution) => { setInstitutions((current) => [...current, institution].sort((a, b) => a.name.localeCompare(b.name))); setShowCreateInstitution(false); }}
            adminKey={authenticatedKey}
            setBusy={setBusy}
            setError={setError}
          />
        )}
      </main>
    </div>
  );
}

function InstitutionsView({ institutions, showCreate, busy, error, onOpen, onShowCreate, onCancel, onCreated, adminKey, setBusy, setError }: {
  institutions: Institution[]; showCreate: boolean; busy: boolean; error: string;
  onOpen: (institution: Institution) => void; onShowCreate: () => void; onCancel: () => void;
  onCreated: (institution: Institution) => void; adminKey: string;
  setBusy: (busy: boolean) => void; setError: (error: string) => void;
}) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      const result = await api.createInstitution(adminKey, {
        name: String(form.get("name")), code: String(form.get("code")), address: String(form.get("address")) || null,
        timezone: String(form.get("timezone")) || "Africa/Accra",
      });
      onCreated(result.institution);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Unable to create institution."); }
    finally { setBusy(false); }
  }
  return <>
    <header className="page-header"><div><p className="eyebrow">Administration</p><h1>Institutions</h1><p>Manage institutions and their optometry facilities.</p></div><button className="primary-button compact" onClick={onShowCreate}><Plus size={17} /> Create institution</button></header>
    {error && <p className="error-message banner">{error}</p>}
    {showCreate && <section className="form-section"><div className="section-heading"><h2>Create institution</h2><p>Add the institution before creating its facilities.</p></div><form onSubmit={submit} className="form-grid"><label>Institution name<input name="name" required /></label><label>Institution code<input name="code" required pattern={"[A-Za-z0-9_\\-]+"} /></label><label className="wide">Address<input name="address" /></label><label>Timezone<input name="timezone" defaultValue="Africa/Accra" required /></label><div className="form-actions"><button type="button" className="secondary-button" onClick={onCancel}>Cancel</button><button className="primary-button" disabled={busy}>{busy ? "Creating..." : "Create institution"}</button></div></form></section>}
    <section className="table-section"><div className="section-heading"><h2>Registered institutions</h2><span>{institutions.length}</span></div>{institutions.length === 0 ? <div className="empty-state"><Building2 size={28} /><h3>No institutions yet</h3><p>Create the first institution to add its facilities.</p></div> : <div className="data-table"><div className="table-row table-head"><span>Institution</span><span>Code</span><span>Status</span><span></span></div>{institutions.map((institution) => <button className="table-row" key={institution.id} onClick={() => onOpen(institution)}><strong>{institution.name}</strong><span>{institution.code}</span><span className="status active">Active</span><span>Open</span></button>)}</div>}</section>
  </>;
}

function FacilitiesView({ institution, facilities, showCreate, busy, error, onBack, onOpen, onShowCreate, onCancel, onCreated, adminKey, setBusy, setError }: {
  institution: Institution; facilities: Facility[]; showCreate: boolean; busy: boolean; error: string;
  onBack: () => void; onOpen: (facility: Facility) => void; onShowCreate: () => void; onCancel: () => void;
  onCreated: (facility: Facility) => void; adminKey: string;
  setBusy: (busy: boolean) => void; setError: (error: string) => void;
}) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      const result = await api.createFacility(adminKey, institution.id, {
        name: String(form.get("name")), code: String(form.get("code")), address: String(form.get("address")) || null,
        timezone: String(form.get("timezone")) || institution.timezone, syncEnabled: true,
      });
      onCreated(result.facility);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Unable to create facility."); }
    finally { setBusy(false); }
  }
  return <>
    <button className="back-button" onClick={onBack}><ArrowLeft size={17} /> All institutions</button>
    <header className="page-header"><div><p className="eyebrow">{institution.code}</p><h1>{institution.name}</h1><p>Select a facility to manage the Main PCs that synchronize within it.</p></div><button className="primary-button compact" onClick={onShowCreate}><Plus size={17} /> Add facility</button></header>
    {error && <p className="error-message banner">{error}</p>}
    {showCreate && <section className="form-section"><div className="section-heading"><h2>Add facility</h2><p>Facility codes are unique within this institution.</p></div><form onSubmit={submit} className="form-grid"><label>Facility name<input name="name" required /></label><label>Facility code<input name="code" required pattern={"[A-Za-z0-9_\\-]+"} /></label><label className="wide">Address<input name="address" /></label><label>Timezone<input name="timezone" defaultValue={institution.timezone} required /></label><div className="form-actions"><button type="button" className="secondary-button" onClick={onCancel}>Cancel</button><button className="primary-button" disabled={busy}>{busy ? "Creating..." : "Create facility"}</button></div></form></section>}
    <section className="table-section"><div className="section-heading"><h2>Facilities</h2><span>{facilities.length}</span></div>{facilities.length === 0 ? <div className="empty-state"><Hospital size={28} /><h3>No facilities yet</h3><p>Add a facility to establish a synchronization boundary.</p></div> : <div className="data-table"><div className="table-row table-head"><span>Facility</span><span>Code</span><span>Sync</span><span></span></div>{facilities.map((facility) => <button className="table-row" key={facility.id} onClick={() => onOpen(facility)}><strong>{facility.name}</strong><span>{facility.code}</span><span className={`status ${facility.syncEnabled ? "active" : "revoked"}`}>{facility.syncEnabled ? "Enabled" : "Disabled"}</span><span>Open</span></button>)}</div>}</section>
  </>;
}

function FacilityView({ institution, facility, devices, enrollment, error, busy, showAddDevice, onBack, onRefresh, onAdd, onCancelAdd, onCreated, adminKey, setBusy, setError }: {
  institution: Institution; facility: Facility; devices: Device[]; enrollment: EnrollmentResult | null; error: string; busy: boolean; showAddDevice: boolean;
  onBack: () => void; onRefresh: () => Promise<void>; onAdd: () => void; onCancelAdd: () => void; onCreated: (result: EnrollmentResult) => void;
  adminKey: string; setBusy: (busy: boolean) => void; setError: (error: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError("");
    try { onCreated(await api.createDevice(adminKey, institution.id, facility.id, { deviceName: String(form.get("deviceName")), locationName: String(form.get("locationName")) || null, expiresInHours: Number(form.get("expiresInHours")) })); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Unable to add Main PC."); }
    finally { setBusy(false); }
  }
  return <>
    <button className="back-button" onClick={onBack}><ArrowLeft size={17} /> {institution.name} facilities</button>
    <header className="page-header"><div><p className="eyebrow">{institution.code} / {facility.code}</p><h1>{facility.name}</h1><p>{facility.address || "No facility address recorded"} · {facility.timezone}</p></div><button className="primary-button compact" onClick={onAdd}><Plus size={17} /> Add Main PC</button></header>
    {error && <p className="error-message banner">{error}</p>}
    {showAddDevice && <section className="form-section"><div className="section-heading"><h2>Add Main PC</h2><p>This creates a pending device and a one-time enrollment token.</p></div><form onSubmit={submit} className="form-grid"><label>Device name<input name="deviceName" placeholder="Main PC - Reception" required /></label><label>Location name<input name="locationName" placeholder="Reception" /></label><label>Token expiry<select name="expiresInHours" defaultValue="24"><option value="1">1 hour</option><option value="8">8 hours</option><option value="24">24 hours</option><option value="72">3 days</option></select></label><div className="form-actions"><button type="button" className="secondary-button" onClick={onCancelAdd}>Cancel</button><button className="primary-button" disabled={busy}>{busy ? "Creating..." : "Create enrollment"}</button></div></form></section>}
    {enrollment && <section className="token-section"><div className="token-icon"><Check size={20} /></div><div><p className="eyebrow">Enrollment created</p><h2>{enrollment.device.deviceName}</h2><p>Use this token on the physical Main PC before {new Date(enrollment.enrollment.expiresAt).toLocaleString()}.</p><div className="token-value"><code>{enrollment.enrollment.token}</code><button title="Copy token" onClick={async () => { await navigator.clipboard.writeText(enrollment.enrollment.token); setCopied(true); }}><Clipboard size={17} /> {copied ? "Copied" : "Copy"}</button></div><p className="token-warning">This token will not be shown again. Store it securely until enrollment is complete.</p></div></section>}
    <section className="table-section"><div className="section-heading"><div><h2>Main PCs</h2><p>Device status refreshes automatically.</p></div><button className="icon-button" title="Refresh devices" onClick={() => void onRefresh()}><RefreshCw size={17} /></button></div>{devices.length === 0 ? <div className="empty-state"><MonitorUp size={28} /><h3>No Main PCs</h3><p>Add a Main PC to generate its enrollment token.</p></div> : <div className="data-table devices"><div className="table-row table-head"><span>Device</span><span>Location</span><span>Status</span><span>Enrolled</span></div>{devices.map((device) => <div className="table-row" key={device.id}><strong>{device.deviceName}</strong><span>{device.locationName || "—"}</span><span className={`status ${device.status}`}>{statusLabels[device.status]}</span><span>{device.enrolledAt ? new Date(device.enrolledAt).toLocaleString() : "—"}</span></div>)}</div>}</section>
  </>;
}
