"""HROne employee attendance API, contract v2.0."""
import json, os, re
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Annotated, Any
from bson import json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv(override=False)
client=MongoClient(os.getenv("MONGO_URI","mongodb://localhost:27017"),tz_aware=True,serverSelectionTimeoutMS=3000)
db=client[os.getenv("MONGO_DB","attendance_db")]
UTC=timezone.utc; IST=timezone(timedelta(hours=5,minutes=30)); PRES=["PRESENT","WFH","ON_DUTY"]

def indexes():
 db.employees.create_index("emp_code",unique=True,name="uq_employee_code")
 db.employees.create_index([("department",1),("joined_on",1)],name="department_joined")
 db.employees.create_index([("joined_on",1),("department",1)],name="joined_department")
 db.attendance_logs.create_index([("emp_code",1),("date",1)],unique=True,name="uq_attendance_employee_date")
 db.attendance_logs.create_index([("date",-1),("emp_code",1)],name="attendance_date_employee")
 db.attendance_logs.create_index([("status",1),("date",-1),("emp_code",1)],name="attendance_status_date_employee")
@asynccontextmanager
async def lifespan(_): indexes(); yield
app=FastAPI(title="Employee Attendance & Analytics API",version="2.0.0",lifespan=lifespan)

class Status(str,Enum):
 PRESENT="PRESENT"; WFH="WFH"; ON_DUTY="ON_DUTY"; ABSENT="ABSENT"; LEAVE="LEAVE"
class Presence(str,Enum): PRESENT="PRESENT"; WFH="WFH"; ON_DUTY="ON_DUTY"
Ms=Annotated[StrictInt,Field(ge=100_000_000_000,le=253_402_300_799_999)]
class EmployeeIn(BaseModel):
 model_config=ConfigDict(extra="ignore")
 emp_code:str=Field(pattern=r"^EMP\d{4,6}$"); name:str=Field(min_length=1,max_length=100)
 email:str=Field(max_length=120,pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"); department:str=Field(min_length=1,max_length=50)
 shift_start:str=Field("09:30",pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"); shift_end:str=Field("18:30",pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"); joined_on:date
 @model_validator(mode="after")
 def shifts(self):
  if self.shift_start==self.shift_end: raise ValueError("shift times must differ")
  return self
class PunchIn(BaseModel):
 model_config=ConfigDict(extra="ignore"); emp_code:str; punched_at:Ms|None=None; status:Presence=Presence.PRESENT
class PunchOut(BaseModel):
 model_config=ConfigDict(extra="ignore"); emp_code:str; punched_at:Ms|None=None
class Regularize(BaseModel):
 model_config=ConfigDict(extra="ignore"); status:Status|None=None; punch_in:Ms|None=None; punch_out:Ms|None=None
 reason:str=Field(min_length=5,max_length=200); regularized_by:str=Field(min_length=1,max_length=50)

def bad(s): raise HTTPException(422,s)
def now(): return datetime.now(UTC).replace(microsecond=0)
def dt(v): return now() if v is None else datetime.fromtimestamp(v//1000,UTC)
def ms(v):
 if v is None:return None
 if v.tzinfo is None:v=v.replace(tzinfo=UTC)
 return int(v.timestamp()*1000)
def rnd(v,n): return float(Decimal(str(v)).quantize(Decimal(1).scaleb(-n),rounding=ROUND_HALF_UP))
def attendance_day(pin,ss,se):
 local=pin.astimezone(IST); d=local.date()
 if se<=ss and local.time().replace(tzinfo=None)<time.fromisoformat(se):d-=timedelta(days=1)
 return d.isoformat()
def shift(day,hm,next_day=False):
 d=date.fromisoformat(day)+(timedelta(days=1) if next_day else timedelta())
 return datetime.combine(d,time.fromisoformat(hm),IST).astimezone(UTC)
def derived(pin,pout,ss,se,day):
 late=0
 if pin:
  seconds=int((pin-shift(day,ss)).total_seconds()); late=seconds//60 if seconds>600 else 0
 if not pin or not pout:return dict(work_hours=None,late_minutes=late,overtime_minutes=0,half_day=False)
 hours=rnd(Decimal(int((pout-pin).total_seconds()))/Decimal(3600),2)
 ot=int((pout-shift(day,se,se<=ss)).total_seconds())//60
 return dict(work_hours=hours,late_minutes=late,overtime_minutes=ot if ot>=30 else 0,half_day=hours<4.5)
def interval(pin,pout):
 if pout and (pout<=pin or pout-pin>timedelta(hours=24)):bad("punch_out must be after punch_in and within 24 hours")
def employee(d): return {**{k:d[k] for k in ("emp_code","name","email","department","shift_start","shift_end","joined_on")},"created_at":ms(d["created_at"])}
def record(d):
 out={k:d.get(k) for k in ("emp_code","date","status","work_hours","late_minutes","overtime_minutes")}
 out.update(punch_in=ms(d.get("punch_in")),punch_out=ms(d.get("punch_out")),half_day=d.get("half_day",False),history=[])
 for h in d.get("history",[]):
  changes={k:{z:(ms(v) if k in ("punch_in","punch_out") else v) for z,v in vals.items()} for k,vals in h["changes"].items()}
  out["history"].append(dict(at=ms(h["at"]),by=h["by"],reason=h["reason"],changes=changes))
 return out
def bounds(month):
 if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])",month):bad("month must be YYYY-MM")
 a=date.fromisoformat(month+"-01"); b=(a.replace(day=28)+timedelta(days=4)).replace(day=1)
 return a.isoformat(),b.isoformat(),a,b-timedelta(days=1)
def weekdays(a,b):
 if a>b:return 0
 return sum((a+timedelta(days=i)).weekday()<5 for i in range((b-a).days+1))

@app.get("/health")
def health():
 try: db.command("ping"); return {"status":"ok"}
 except PyMongoError as e: raise HTTPException(503,"database unavailable") from e
@app.post("/employees",status_code=201)
def create_employee(body:EmployeeIn):
 d=body.model_dump(mode="json"); d["created_at"]=now()
 try:db.employees.insert_one(d)
 except DuplicateKeyError as e:raise HTTPException(409,"emp_code already exists") from e
 return employee(d)
@app.get("/employees")
def list_employees(department:str|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 q={"department":department} if department is not None else {}; cur=db.employees.find(q,{"_id":0}).sort("emp_code",1).skip((page-1)*page_size).limit(page_size)
 return dict(items=[employee(x) for x in cur],total=db.employees.count_documents(q),page=page,page_size=page_size)
@app.post("/attendance/punch-in",status_code=201)
def punch_in(body:PunchIn):
 e=db.employees.find_one({"emp_code":body.emp_code})
 if not e:raise HTTPException(404,"employee not found")
 pin=dt(body.punched_at); day=attendance_day(pin,e["shift_start"],e["shift_end"])
 d=dict(emp_code=body.emp_code,date=day,status=body.status.value,punch_in=pin,punch_out=None,history=[]); d.update(derived(pin,None,e["shift_start"],e["shift_end"],day))
 try:db.attendance_logs.insert_one(d)
 except DuplicateKeyError as x:raise HTTPException(409,"already punched in for this date") from x
 return record(d)
@app.post("/attendance/punch-out")
def punch_out(body:PunchOut):
 e=db.employees.find_one({"emp_code":body.emp_code})
 if not e:raise HTTPException(404,"employee not found")
 out=dt(body.punched_at); old=db.attendance_logs.find_one({"emp_code":body.emp_code,"punch_in":{"$lte":out}},sort=[("punch_in",-1)])
 if not old:raise HTTPException(404,"no punch-in found")
 if old.get("punch_out") is not None:raise HTTPException(409,"record already punched out")
 interval(old["punch_in"],out); fields={"punch_out":out,**derived(old["punch_in"],out,e["shift_start"],e["shift_end"],old["date"])}
 got=db.attendance_logs.find_one_and_update({"_id":old["_id"],"punch_out":None},{"$set":fields},return_document=ReturnDocument.AFTER)
 if not got:raise HTTPException(409,"record already punched out")
 return record(got)
def att_filter(emp_code,date_from,date_to,status):
 if date_from and date_to and date_from>date_to:bad("date_from must not be after date_to")
 q={}
 if emp_code is not None:q["emp_code"]=emp_code
 if status is not None:q["status"]=status.value
 if date_from or date_to:
  q["date"]={}
  if date_from:q["date"]["$gte"]=date_from.isoformat()
  if date_to:q["date"]["$lte"]=date_to.isoformat()
 return q
@app.get("/attendance")
def list_attendance(emp_code:str|None=None,date_from:date|None=None,date_to:date|None=None,status:Status|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 q=att_filter(emp_code,date_from,date_to,status); cur=db.attendance_logs.find(q,{"_id":0}).sort([("date",-1),("emp_code",1)]).skip((page-1)*page_size).limit(page_size)
 return dict(items=[record(x) for x in cur],total=db.attendance_logs.count_documents(q),page=page,page_size=page_size)
@app.patch("/attendance/{emp_code}/{date}")
def regularize(body:Regularize,emp_code:str,day:date=Path(alias="date")):
 ds=day.isoformat(); e=db.employees.find_one({"emp_code":emp_code}); old=db.attendance_logs.find_one({"emp_code":emp_code,"date":ds})
 if not e or not old:raise HTTPException(404,"employee or attendance record not found")
 supplied=body.model_fields_set; status=body.status.value if "status" in supplied and body.status else old["status"]
 pin=dt(body.punch_in) if "punch_in" in supplied and body.punch_in is not None else (None if "punch_in" in supplied else old.get("punch_in"))
 pout=dt(body.punch_out) if "punch_out" in supplied and body.punch_out is not None else (None if "punch_out" in supplied else old.get("punch_out"))
 if status in ("ABSENT","LEAVE"):
  if ("punch_in" in supplied and body.punch_in is not None) or ("punch_out" in supplied and body.punch_out is not None):bad("absence cannot have punch times")
  pin=pout=None
 elif pin is None:bad("presence requires punch_in")
 if pin and attendance_day(pin,e["shift_start"],e["shift_end"])!=ds:bad("punch_in does not belong to attendance date")
 if pin:interval(pin,pout)
 final=dict(status=status,punch_in=pin,punch_out=pout); final.update(derived(pin,pout,e["shift_start"],e["shift_end"],ds))
 keys=("status","punch_in","punch_out","work_hours","late_minutes","overtime_minutes","half_day"); changes={k:{"from":old.get(k,False if k=="half_day" else None),"to":final[k]} for k in keys if old.get(k,False if k=="half_day" else None)!=final[k]}
 if not changes:bad("request changes nothing")
 hist=old.get("history",[]); guard={"history":hist} if hist else {"$or":[{"history":[]},{"history":{"$exists":False}}]}
 got=db.attendance_logs.find_one_and_update({"_id":old["_id"],**guard},{"$set":final,"$push":{"history":dict(at=now(),by=body.regularized_by,reason=body.reason,changes=changes)}},return_document=ReturnDocument.AFTER)
 if not got:raise HTTPException(409,"record modified concurrently")
 return record(got)

def monthly_pipe(code,a,b):
 wd={"$and":[{"$gte":[{"$dayOfWeek":{"$dateFromString":{"dateString":"$date"}}},2]},{"$lte":[{"$dayOfWeek":{"$dateFromString":{"dateString":"$date"}}},6]}]}; pres={"$in":["$status",PRES]}
 return [{"$match":{"emp_code":code,"date":{"$gte":a,"$lt":b}}},{"$group":{"_id":None,"present_days":{"$sum":{"$cond":[{"$and":[wd,pres]},{"$cond":[{"$ifNull":["$half_day",False]},.5,1]},0]}},"leave_days":{"$sum":{"$cond":[{"$eq":["$status","LEAVE"]},1,0]}},"late_count":{"$sum":{"$cond":[{"$gt":["$late_minutes",0]},1,0]}},"total_late_minutes":{"$sum":{"$ifNull":["$late_minutes",0]}},"total_overtime_minutes":{"$sum":{"$ifNull":["$overtime_minutes",0]}}}},{"$unset":"_id"}]
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code:str,month:str):
 a,b,first,last=bounds(month); e=db.employees.find_one({"emp_code":emp_code})
 if not e:raise HTTPException(404,"employee not found")
 rows=list(db.attendance_logs.aggregate(monthly_pipe(emp_code,a,b))); vals=rows[0] if rows else dict(present_days=0,leave_days=0,late_count=0,total_late_minutes=0,total_overtime_minutes=0)
 work=weekdays(max(first,date.fromisoformat(e["joined_on"])),last); return dict(emp_code=emp_code,month=month,working_days=work,**vals,attendance_pct=rnd(vals["present_days"]/work*100,2) if work else None)
def summary_pipe(a,b,last,department):
 match={"joined_on":{"$lte":last}};
 if department is not None:match["department"]=department
 wd={"$and":[{"$gte":[{"$dayOfWeek":{"$dateFromString":{"dateString":"$logs.date"}}},2]},{"$lte":[{"$dayOfWeek":{"$dateFromString":{"dateString":"$logs.date"}}},6]}]}; pres={"$in":["$logs.status",PRES]}; valid={"$and":[pres,{"$ne":["$logs.work_hours",None]}]}
 return [{"$match":match},{"$lookup":{"from":"attendance_logs","let":{"c":"$emp_code"},"pipeline":[{"$match":{"$expr":{"$and":[{"$eq":["$emp_code","$$c"]},{"$gte":["$date",a]},{"$lt":["$date",b]}]}}}],"as":"logs"}},{"$unwind":{"path":"$logs","preserveNullAndEmptyArrays":True}},{"$group":{"_id":"$department","employees":{"$addToSet":"$emp_code"},"present_days":{"$sum":{"$cond":[{"$and":[wd,pres]},{"$cond":[{"$ifNull":["$logs.half_day",False]},.5,1]},0]}},"work_sum":{"$sum":{"$cond":[valid,"$logs.work_hours",0]}},"work_count":{"$sum":{"$cond":[valid,1,0]}},"late_count":{"$sum":{"$cond":[{"$gt":["$logs.late_minutes",0]},1,0]}},"total_late_minutes":{"$sum":{"$ifNull":["$logs.late_minutes",0]}},"leave_count":{"$sum":{"$cond":[{"$eq":["$logs.status","LEAVE"]},1,0]}},"on_duty_count":{"$sum":{"$cond":[{"$eq":["$logs.status","ON_DUTY"]},1,0]}}}},{"$project":{"_id":0,"department":"$_id","headcount":{"$size":"$employees"},"present_days":1,"avg_work_hours":{"$cond":[{"$gt":["$work_count",0]},{"$round":[{"$divide":["$work_sum","$work_count"]},2]},None]},"late_count":1,"total_late_minutes":1,"leave_count":1,"on_duty_count":1}},{"$sort":{"department":1}}]
@app.get("/analytics/departments/summary")
def department_summary(month:str,department:str|None=None):
 a,b,_,last=bounds(month); return {"month":month,"items":list(db.employees.aggregate(summary_pipe(a,b,last.isoformat(),department)))}
def leaderboard_pipe(a,b,limit,department):
 em={"$expr":{"$eq":["$emp_code","$$c"]}}
 if department is not None:em={"$and":[em,{"department":department}]}
 return [{"$match":{"date":{"$gte":a,"$lt":b},"late_minutes":{"$gt":0}}},{"$group":{"_id":"$emp_code","total_late_minutes":{"$sum":"$late_minutes"},"late_count":{"$sum":1}}},{"$lookup":{"from":"employees","let":{"c":"$_id"},"pipeline":[{"$match":em}],"as":"e"}},{"$unwind":"$e"},{"$setWindowFields":{"sortBy":{"total_late_minutes":-1},"output":{"rank":{"$rank":{}}}}},{"$match":{"rank":{"$lte":limit}}},{"$sort":{"total_late_minutes":-1,"_id":1}},{"$project":{"_id":0,"rank":1,"emp_code":"$_id","name":"$e.name","department":"$e.department","total_late_minutes":1,"late_count":1}}]
@app.get("/analytics/leaderboard/late")
def leaderboard(month:str,limit:int=Query(10,ge=1,le=50),department:str|None=None):
 a,b,_,_=bounds(month); return {"month":month,"items":list(db.attendance_logs.aggregate(leaderboard_pipe(a,b,limit,department)))}
def trend_pipe(department,first,last):
 lo=datetime.combine(first,time(),UTC); hi=datetime.combine(last+timedelta(days=1),time(),UTC)
 return [{"$match":{"department":department}},{"$limit":1},{"$project":{"_id":0,"day":{"$literal":lo}}},{"$densify":{"field":"day","range":{"step":1,"unit":"day","bounds":[lo,hi]}}},{"$set":{"date":{"$dateToString":{"date":"$day","format":"%Y-%m-%d"}}}},{"$lookup":{"from":"employees","let":{"d":"$date"},"pipeline":[{"$match":{"department":department}},{"$match":{"$expr":{"$lte":["$joined_on","$$d"]}}},{"$count":"n"}],"as":"h"}},{"$lookup":{"from":"attendance_logs","let":{"d":"$date"},"pipeline":[{"$match":{"$expr":{"$eq":["$date","$$d"]}}},{"$lookup":{"from":"employees","localField":"emp_code","foreignField":"emp_code","as":"e"}},{"$unwind":"$e"},{"$match":{"e.department":department}},{"$group":{"_id":None,"present_count":{"$sum":{"$cond":[{"$in":["$status",PRES]},{"$cond":[{"$ifNull":["$half_day",False]},.5,1]},0]}},"late_count":{"$sum":{"$cond":[{"$gt":["$late_minutes",0]},1,0]}}}}],"as":"l"}},{"$set":{"headcount":{"$ifNull":[{"$first":"$h.n"},0]},"present_count":{"$ifNull":[{"$first":"$l.present_count"},0]},"late_count":{"$ifNull":[{"$first":"$l.late_count"},0]},"is_working_day":{"$in":[{"$dayOfWeek":"$day"},[2,3,4,5,6]]}}},{"$set":{"attendance_rate":{"$cond":[{"$and":["$is_working_day",{"$gt":["$headcount",0]}]},{"$round":[{"$divide":["$present_count","$headcount"]},4]},None]}}},{"$setWindowFields":{"sortBy":{"day":1},"output":{"moving_avg_7d":{"$avg":"$attendance_rate","window":{"documents":[-6,0]}}}}},{"$project":{"_id":0,"date":1,"is_working_day":1,"headcount":1,"present_count":1,"late_count":1,"attendance_rate":1,"moving_avg_7d":{"$cond":[{"$ne":["$moving_avg_7d",None]},{"$round":["$moving_avg_7d",4]},None]}}}]
def trend_range(a,b):
 if b<a:bad("to must not be before from")
 if (b-a).days+1>92:bad("range cannot exceed 92 days")
@app.get("/analytics/departments/{department}/trend")
def trend(department:str,date_from:date=Query(alias="from"),date_to:date=Query(alias="to")):
 trend_range(date_from,date_to)
 if not db.employees.find_one({"department":department}):raise HTTPException(404,"department not found")
 return {"department":department,"items":list(db.employees.aggregate(trend_pipe(department,date_from,date_to)))}
def explain_agg(coll,pipe):return db.command({"explain":{"aggregate":coll,"pipeline":pipe,"cursor":{}},"verbosity":"executionStats"})
@app.get("/admin/explain/{endpoint}")
def explain(endpoint:str,emp_code:str|None=None,month:str|None=None,department:str|None=None,limit:int=Query(10,ge=1,le=50),date_from:date|None=None,date_to:date|None=None,status:Status|None=None,from_:date|None=Query(None,alias="from"),to:date|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
 if endpoint=="attendance_list":r=db.command({"explain":{"find":"attendance_logs","filter":att_filter(emp_code,date_from,date_to,status),"sort":{"date":-1,"emp_code":1},"skip":(page-1)*page_size,"limit":page_size},"verbosity":"executionStats"})
 elif endpoint=="employee_monthly":
  if not emp_code or not month:bad("emp_code and month required")
  a,b,_,_=bounds(month);r=explain_agg("attendance_logs",monthly_pipe(emp_code,a,b))
 elif endpoint=="department_summary":
  if not month:bad("month required")
  a,b,_,last=bounds(month);r=explain_agg("employees",summary_pipe(a,b,last.isoformat(),department))
 elif endpoint=="late_leaderboard":
  if not month:bad("month required")
  a,b,_,_=bounds(month);r=explain_agg("attendance_logs",leaderboard_pipe(a,b,limit,department))
 elif endpoint=="department_trend":
  if not department or not from_ or not to:bad("department, from and to required")
  trend_range(from_,to);r=explain_agg("employees",trend_pipe(department,from_,to))
 else:bad("unknown endpoint")
 return json.loads(json_util.dumps(r))
