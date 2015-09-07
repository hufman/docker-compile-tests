FROM alpine:latest
ENTRYPOINT ["/bin/sh", "-c"]
RUN mkdir /results_out
ENV test yay
RUN set | grep test > /results_out/test
