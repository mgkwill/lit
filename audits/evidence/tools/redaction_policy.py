"""Reproduce public excerpt redactions with an owner-supplied private policy."""
import re


def sanitize(data, plan):
    owner = re.escape(plan['private_owner'].encode())
    path = re.compile(rb'/(?:Users|home)/' + owner + rb'(?:/[^\s`<>"\'\\\[\]{}()]*)?')
    windows = re.compile(rb'[A-Za-z]:(?:\\\\|\\)Users(?:\\\\|\\)' + owner +
                         rb'(?:(?:\\\\|\\)[^\s`<>"\']*)?', re.I)
    data = path.sub(b'<private-path>', data)
    data = windows.sub(b'<private-path>', data)
    for old, new in plan['host_map'].items():
        old, new = old.encode(), new.encode()
        variants = [(old, new), (old.upper(), new.upper()),
                    (old.replace(b'-', b''), new.replace(b'-', b'')),
                    (old.replace(b'-', b'_'), new.replace(b'-', b'_'))]
        for before, after in variants:
            data = re.sub(rb'(?<![A-Za-z0-9])' + re.escape(before) + rb'(?![A-Za-z0-9])',
                          lambda match, after=after: after, data)
    for old, new in plan['network_map'].items():
        data = re.sub(rb'(?<![0-9.])' + re.escape(old.encode()) + rb'(?![0-9.])',
                      lambda match, new=new: new.encode(), data)
    for old, new in plan['deployment_alias_map'].items():
        data = data.replace(old.encode(), new.encode())
    return data
