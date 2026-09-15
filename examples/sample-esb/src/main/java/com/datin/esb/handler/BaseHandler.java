package com.datin.esb.handler;

public abstract class BaseHandler<REQ, RES> {
    protected abstract RES execute(REQ request);

    public RES handle(Object raw) {
        @SuppressWarnings("unchecked")
        REQ req = (REQ) raw;
        return execute(req);
    }
}
