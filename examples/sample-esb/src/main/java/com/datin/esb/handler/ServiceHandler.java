package com.datin.esb.handler;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;

/** In-house dispatcher marker: the ESB routes requests to classes carrying this annotation. */
@Retention(RetentionPolicy.RUNTIME)
public @interface ServiceHandler {
    String path();
    String method() default "POST";
}
