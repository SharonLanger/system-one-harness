"""Bounded summary representation shared by scan budgeting and transport."""
def summarize(record,limits,ref):
    f=record.fields
    data={k:v[:limits.field_preview] if isinstance(v,str) else v for k,v in f.items() if k!='details'}
    data['message']=f['message'][:limits.message_preview]
    data['message_truncated']=len(f['message'])>limits.message_preview
    data['fields_truncated']=[k for k,v in f.items() if k not in ('details','message') and isinstance(v,str) and len(v)>limits.field_preview]
    data.update(ref=ref,source={'path':record.source.path,'offset':record.source.offset,'length':record.source.length})
    return data
