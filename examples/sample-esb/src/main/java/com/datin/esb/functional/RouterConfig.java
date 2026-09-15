package com.datin.esb.functional;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.server.RouterFunction;
import org.springframework.web.reactive.function.server.ServerResponse;

import static org.springframework.web.reactive.function.server.RequestPredicates.GET;
import static org.springframework.web.reactive.function.server.RequestPredicates.POST;
import static org.springframework.web.reactive.function.server.RouterFunctions.route;

@Configuration
public class RouterConfig {
    @Bean
    public RouterFunction<ServerResponse> documentRoutes(DocumentHandler handler) {
        return route(GET("/functional/documents/{id}"), handler::get)
                .andRoute(POST("/functional/documents"), handler::create);
    }
}
