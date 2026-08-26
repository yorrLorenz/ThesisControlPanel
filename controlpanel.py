import tkinter as tk
from tkinter import ttk
import serial, serial.tools.list_ports, threading, time
from collections import deque
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import csv
from datetime import datetime

BAUD = 115200
MAXPTS = 400

class SerialLink:
    def __init__(self):
        self.ser=None; self.running=False
        self.latest={'pwm':0,'i':0,'t1':0,'t2':0,'force':0,'flux':0,'fault':0}
        self.hist={k:deque(maxlen=MAXPTS) for k in ('t','i','t1','t2','force','flux')}
        self.t0=time.time(); self.lock=threading.Lock()
        self.recording=False; self.rec_file=None; self.rec_writer=None
        self.rec_rows=0; self.rec_path=None; self.rec_lock=threading.Lock()

    def open(self,port):
        self.ser=serial.Serial(port,BAUD,timeout=0.2)
        self.running=True; self.t0=time.time()
        threading.Thread(target=self._read,daemon=True).start()

    def close(self):
        self.stop_rec()
        self.running=False; time.sleep(0.25)
        if self.ser: self.ser.close(); self.ser=None

    def send(self,s):
        if self.ser and self.ser.is_open:
            try: self.ser.write((s+'\n').encode())
            except: pass

    def start_rec(self,path):
        with self.rec_lock:
            self.rec_file=open(path,'w',newline='')
            self.rec_writer=csv.writer(self.rec_file)
            self.rec_writer.writerow(['timestamp','elapsed_s','pwm','current_A',
                't1_casing_C','t2_magnet_C','force_N','flux_mT','fault'])
            self.rec_rows=0; self.rec_path=path; self.recording=True

    def stop_rec(self):
        with self.rec_lock:
            self.recording=False
            if self.rec_file: self.rec_file.flush(); self.rec_file.close()
            self.rec_file=None; self.rec_writer=None

    def _read(self):
        while self.running:
            try: line=self.ser.readline().decode(errors='ignore').strip()
            except: break
            if not line.startswith('D,'): continue
            p=line.split(',')
            if len(p)<8: continue
            try:
                pwm=int(p[1]); i=float(p[2]); t1=float(p[3]); t2=float(p[4])
                f=float(p[5]); b=float(p[6]); flt=int(p[7])
            except: continue
            t=time.time()-self.t0
            with self.lock:
                self.latest.update(pwm=pwm,i=i,t1=t1,t2=t2,force=f,flux=b,fault=flt)
                for k,v in (('t',t),('i',i),('t1',t1),('t2',t2),('force',f),('flux',b)):
                    self.hist[k].append(v)
            with self.rec_lock:
                if self.recording and self.rec_writer:
                    self.rec_writer.writerow([datetime.now().isoformat(timespec='milliseconds'),
                        f'{t:.3f}',pwm,f'{i:.3f}',f'{t1:.1f}',f'{t2:.1f}',f'{f:.3f}',f'{b:.2f}',flt])
                    self.rec_rows+=1
                    if self.rec_rows%10==0: self.rec_file.flush()

class App:
    SENSORS={'force':('Force','N','force','Tare  (TL)','TL'),
             'flux' :('Flux Density','mT','flux','Zero  (ZH)','ZH'),
             'temp' :('Temperature','°C','t1',None,None)}

    def __init__(self,root):
        self.link=SerialLink(); self.root=root; self.sensor='force'
        self.last_send=0
        root.title("Grip Rehab — Sensor Control Panel")
        root.geometry("1120x680"); root.minsize(940,580)
        root.bind('<Escape>', lambda e:self._kill())
        self._topbar()
        body=ttk.Frame(root); body.pack(fill='both',expand=True,padx=8,pady=(0,8))
        body.columnconfigure(0,weight=1,uniform='h')
        body.columnconfigure(1,weight=1,uniform='h')
        body.rowconfigure(0,weight=1)
        self._pwm_panel(body); self._sensor_panel(body)
        self._build_lines()
        root.after(80,self._tick)

    # ---------- top bar ----------
    def _topbar(self):
        bar=ttk.Frame(self.root); bar.pack(fill='x',padx=8,pady=8)
        ttk.Label(bar,text="Port:").pack(side='left')
        self.port=ttk.Combobox(bar,width=22,state='readonly'); self.port.pack(side='left',padx=4)
        ttk.Button(bar,text="↻",width=3,command=self._refresh).pack(side='left')
        self.connbtn=ttk.Button(bar,text="Connect",command=self._connect); self.connbtn.pack(side='left',padx=6)
        self.status=ttk.Label(bar,text="Disconnected",foreground='#b00'); self.status.pack(side='left',padx=8)
        self.recbtn=tk.Button(bar,text="● Record",fg='#c62828',font=('Segoe UI',10,'bold'),
            command=self._toggle_rec)
        self.recbtn.pack(side='right',padx=6)
        self.reclbl=ttk.Label(bar,text=""); self.reclbl.pack(side='right',padx=4)
        self._refresh()

    def _refresh(self):
        ports=[p.device for p in serial.tools.list_ports.comports()]
        self.port['values']=ports
        if ports and not self.port.get(): self.port.set(ports[0])

    def _connect(self):
        if self.link.ser:
            self.link.close(); self.connbtn['text']="Connect"
            self.recbtn.config(text="● Record",bg=self.root.cget('bg'),fg='#c62828')
            self.status.config(text="Disconnected",foreground='#b00'); return
        try:
            self.link.open(self.port.get())
            self.connbtn['text']="Disconnect"
            self.status.config(text="Connected",foreground='#080')
        except Exception as e:
            self.status.config(text=f"Error: {e}",foreground='#b00')

    # ---------- left: PWM ----------
    def _pwm_panel(self,parent):
        f=ttk.LabelFrame(parent,text="PWM / Coil Control")
        f.grid(row=0,column=0,sticky='nsew',padx=(0,4)); f.columnconfigure(0,weight=1)

        self.killbtn=tk.Button(f,text="⏻  KILL",bg='#c62828',fg='white',
            font=('Segoe UI',22,'bold'),height=2,relief='raised',command=self._kill)
        self.killbtn.grid(row=0,column=0,sticky='ew',padx=12,pady=12)

        self.en=tk.BooleanVar(value=False)
        ttk.Checkbutton(f,text="Output enabled",variable=self.en,
            command=self._enable).grid(row=1,column=0,sticky='w',padx=14)

        self.duty=tk.IntVar(value=0)
        self.dutylbl=ttk.Label(f,text="Duty: 0 / 255   (0.0%)",font=('Segoe UI',13,'bold'))
        self.dutylbl.grid(row=2,column=0,sticky='w',padx=14,pady=(10,0))
        ttk.Scale(f,from_=0,to=255,variable=self.duty,orient='horizontal',
            command=self._slider).grid(row=3,column=0,sticky='ew',padx=14,pady=6)

        fine=ttk.Frame(f); fine.grid(row=4,column=0,sticky='ew',padx=14,pady=(2,8))
        ttk.Label(fine,text="Set duty:").pack(side='left')
        self.dutyentry=ttk.Entry(fine,width=6,justify='right')
        self.dutyentry.insert(0,'0'); self.dutyentry.pack(side='left',padx=4)
        self.dutyentry.bind('<Return>',self._apply_entry)
        self.dutyentry.bind('<Up>',lambda e:self._nudge(1))
        self.dutyentry.bind('<Down>',lambda e:self._nudge(-1))
        ttk.Button(fine,text="Set",width=4,command=self._apply_entry).pack(side='left',padx=(0,10))
        for d in (-10,-1,1,10):
            ttk.Button(fine,text=f"{d:+d}",width=4,
                command=lambda v=d:self._nudge(v)).pack(side='left',padx=1)

        box=ttk.Frame(f); box.grid(row=5,column=0,sticky='ew',padx=14,pady=14)
        box.columnconfigure(1,weight=1)
        self.rd={}
        for i,(k,txt) in enumerate([('i','Current'),('t1','T1 casing'),('t2','T2 magnet')]):
            ttk.Label(box,text=txt+':').grid(row=i,column=0,sticky='w',pady=3)
            self.rd[k]=ttk.Label(box,text='—',font=('Consolas',16,'bold'))
            self.rd[k].grid(row=i,column=1,sticky='e')

        self.faultlbl=tk.Label(f,text="",fg='white',font=('Segoe UI',12,'bold'))
        self.faultlbl.grid(row=6,column=0,sticky='ew',padx=14)
        self.resetbtn=ttk.Button(f,text="Clear thermal fault",command=lambda:self.link.send('R'))

    def _set_duty(self,v,force=False):
        v=max(0,min(255,int(round(v))))
        self._updating=True; self.duty.set(v); self._updating=False
        self.dutylbl.config(text=f"Duty: {v} / 255   ({v/255*100:.1f}%)")
        if self.dutyentry.get()!=str(v):
            self.dutyentry.delete(0,'end'); self.dutyentry.insert(0,str(v))
        if self.en.get():
            now=time.time()
            if force or now-self.last_send>0.02:
                self.link.send(f'P{v}'); self.last_send=now

    def _slider(self,_=None):
        if getattr(self,'_updating',False): return
        self._set_duty(self.duty.get())

    def _apply_entry(self,_=None):
        try: v=int(float(self.dutyentry.get()))
        except ValueError: v=self.duty.get()
        self._set_duty(v,force=True)

    def _nudge(self,d):
        self._set_duty(self.duty.get()+d,force=True)

    def _enable(self):
        self.link.send('E1' if self.en.get() else 'E0')
        if self.en.get(): self._set_duty(self.duty.get(),force=True)

    def _kill(self):
        self.link.send('K'); self.en.set(False); self._set_duty(0)

    # ---------- right: sensor ----------
    def _sensor_panel(self,parent):
        f=ttk.LabelFrame(parent,text="Sensor Monitor")
        f.grid(row=0,column=1,sticky='nsew',padx=(4,0))
        f.columnconfigure(0,weight=1); f.rowconfigure(2,weight=1)

        sel=ttk.Frame(f); sel.grid(row=0,column=0,sticky='w',padx=12,pady=10)
        self.sensvar=tk.StringVar(value='force')
        for key,(name,*_ ) in self.SENSORS.items():
            ttk.Radiobutton(sel,text=name,value=key,variable=self.sensvar,
                command=self._switch).pack(side='left',padx=6)

        self.big=ttk.Label(f,text="—",font=('Consolas',34,'bold'))
        self.big.grid(row=1,column=0,pady=(0,4))

        self.fig=Figure(figsize=(5,3),dpi=100); self.ax=self.fig.add_subplot(111)
        self.canvas=FigureCanvasTkAgg(self.fig,master=f)
        self.canvas.get_tk_widget().grid(row=2,column=0,sticky='nsew',padx=10,pady=6)

        self.tzbtn=ttk.Button(f,text="Tare  (TL)",command=self._tare_zero)
        self.tzbtn.grid(row=3,column=0,pady=(0,12))

    def _switch(self):
        self.sensor=self.sensvar.get(); self._build_lines()
        _,_,_,btxt,_=self.SENSORS[self.sensor]
        if btxt: self.tzbtn.config(text=btxt,state='normal')
        else: self.tzbtn.config(text="—",state='disabled')

    def _tare_zero(self):
        cmd=self.SENSORS[self.sensor][4]
        if cmd: self.link.send(cmd)

    def _build_lines(self):
        self.ax.clear(); self.ax.grid(alpha=.3); self.ax.set_xlabel('s')
        if self.sensor=='temp':
            (self.l1,)=self.ax.plot([],[],label='T1 casing')
            (self.l2,)=self.ax.plot([],[],label='T2 magnet')
            self.ax.set_ylabel('°C'); self.ax.legend(loc='upper left',fontsize=8)
            self.keys=('t1','t2')
        else:
            _,unit,hk,*_=self.SENSORS[self.sensor]
            (self.l1,)=self.ax.plot([],[]); self.l2=None
            self.ax.set_ylabel(unit); self.keys=(hk,)
        self.fig.tight_layout(); self.canvas.draw()

    # ---------- loop ----------
    def _tick(self):
        L=self.link
        with L.lock:
            lt=dict(L.latest)
            t=list(L.hist['t'])
            ys=[list(L.hist[k]) for k in self.keys]

        if L.recording:
            self.reclbl.config(text=f"REC ● {L.rec_rows} rows")

        self.rd['i'].config(text=f"{lt['i']:.2f} A")
        self._temp_label('t1',lt['t1'],55,60)
        self._temp_label('t2',lt['t2'],65,70)

        if self.sensor=='temp':
            self.big.config(text=f"{lt['t1']:.1f} / {lt['t2']:.1f} °C")
        else:
            _,unit,hk,*_=self.SENSORS[self.sensor]
            self.big.config(text=f"{lt[hk]:.2f} {unit}")

        if lt['fault']:
            self.faultlbl.config(text=" THERMAL FAULT — coil de-energized ",bg='#c62828')
            self.resetbtn.grid(row=7,column=0,pady=6)
        else:
            self.faultlbl.config(text="",bg=self.root.cget('bg')); self.resetbtn.grid_forget()

        if t:
            self.l1.set_data(t,ys[0])
            if self.l2 is not None and len(ys)>1: self.l2.set_data(t,ys[1])
            self.ax.set_xlim(max(0,t[-1]-30),max(30,t[-1]))
            allv=[v for y in ys for v in y] or [0]
            lo,hi=min(allv),max(allv)
            if lo==hi: lo-=1; hi+=1
            m=(hi-lo)*0.1
            self.ax.set_ylim(lo-m,hi+m)
            self.canvas.draw_idle()

        self.root.after(80,self._tick)

    def _temp_label(self,k,val,warn,cut):
        c='#111'
        if val>=cut: c='#c62828'
        elif val>=warn: c='#e08000'
        self.rd[k].config(text=f"{val:.1f} °C",foreground=c)

    def _toggle_rec(self):
        if not self.link.ser:
            self.reclbl.config(text="Connect first"); return
        if not self.link.recording:
            fn=f"grip_log_{datetime.now():%Y%m%d_%H%M%S}.csv"
            try: self.link.start_rec(fn)
            except Exception as e:
                self.reclbl.config(text=f"REC error: {e}"); return
            self.recbtn.config(text="■ Stop",bg='#c62828',fg='white')
        else:
            self.link.stop_rec()
            self.recbtn.config(text="● Record",bg=self.root.cget('bg'),fg='#c62828')
            self.reclbl.config(text=f"Saved {self.link.rec_path}")

if __name__=='__main__':
    root=tk.Tk(); App(root); root.mainloop()