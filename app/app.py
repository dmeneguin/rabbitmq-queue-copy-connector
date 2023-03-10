import logging
import logging.config
import sys
import threading
import traceback
import os

from pika import ConnectionParameters, PlainCredentials
from pika.adapters import BlockingConnection
from pika.channel import Channel

logging.config.dictConfig(
    {
        "version": 1,
        "formatters": {
            "simple": {
                "format": "%(asctime)s [%(levelname)s] [thread:%(threadName)s] %(msg)s"
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
            }
        },
        "loggers": {
            __name__: {
                "handlers": ["console"],
                "propagate": False,
                "level": "INFO",
            }
        },
        "root": {"handlers": ["console"]},
    }
)

logger = logging.getLogger(__name__)

DOWNSTREAM_EXCHANGE=os.environ['DOWNSTREAM_EXCHANGE']
DOWNSTREAM_ROUTING_KEY=os.environ['DOWNSTREAM_ROUTING_KEY']
UPSTREAM_QUEUE=os.environ['UPSTREAM_QUEUE']
UPSTREAM_HOST=os.environ['UPSTREAM_HOST']
DOWNSTREAM_HOST=os.environ['DOWNSTREAM_HOST']
class Publisher(threading.Thread):
    def __init__(
        self,
        connection_params: ConnectionParameters,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.daemon = True
        self.is_running = True
        self.name = "Publisher"
        self.exchange = DOWNSTREAM_EXCHANGE
        self.routing_key = DOWNSTREAM_ROUTING_KEY
        self.connection = BlockingConnection(connection_params)
        self.channel = self.connection.channel()
        self.channel.confirm_delivery()

    def run(self):
        # Uncomment `pass` and comment the `while` to simulate
        # the not handling heartbeats properly.
        # Note that if we're NOT calling `.process_data_events`,
        # calling `Publisher.publish` won't work either, because
        # it schedules the actual publishing as a callback (self._publish).
        # That callback never gets run.
        #
        # The likely reason why publishing works at all without
        # `.process_data_events` is that in most examples in the wild
        # people just call `channel.basic_publish` directly.

        # pass
        while self.is_running:        
            try:            
                self.connection.process_data_events(time_limit=1)
            except Exception as e:
                traceback.print_exception(*sys.exc_info())
                self.is_running = False

    def _publish(self, message):
        logger.info("Calling '_publish'")
        self.channel.basic_publish(exchange=self.exchange,routing_key=self.routing_key,body=message.encode())

    def publish(self, message):
        logger.info("Calling 'publish'")
        self.connection.add_callback_threadsafe(lambda: self._publish(message))

    def stop(self):
        logger.info("Stopping...")
        self.is_running = False
        # Wait until all the data events have been processed
        self.connection.process_data_events(time_limit=1)
        if self.connection.is_open:
            self.connection.close()
            logger.info("Connection closed")
        logger.info("Stopped") 


class Consumer:
    def __init__(
        self,
        connection_params: ConnectionParameters,
        downstream_connection_params: ConnectionParameters
    ):
        self.publisher = Publisher(downstream_connection_params)
        self.queue = UPSTREAM_QUEUE
        self.connection = BlockingConnection(connection_params)
        self.channel = self.connection.channel()
        self.channel.basic_qos(prefetch_count=1)

    def start(self):
        self.channel.basic_consume(
            queue=self.queue, on_message_callback=self.on_message
        )
        try:
            self.publisher.start()
            logger.info(f"Started Publisher")
            self.channel.start_consuming()
        except KeyboardInterrupt:
            logger.info("Warm shutdown requested...")
        except Exception:
            traceback.print_exception(*sys.exc_info())
        finally:
            self.stop()

    def on_message(self, _channel: Channel, m, _properties, body):
        try:
            message = body.decode()
            logger.info(f"Got: {message!r}")
            if self.publisher:
                self.publisher.publish(message)
            else:
                logger.info(f"No publisher provided, printing message: {message!r}")
            self.channel.basic_ack(delivery_tag=m.delivery_tag)
        except Exception as e:
            self.channel.basic_nack(delivery_tag=m.delivery_tag, requeue=True)
            raise e

    def stop(self):
        logger.info("Stopping consuming...")
        if self.connection.is_open:
            logger.info("Closing connection...")
            self.connection.close()

        if self.publisher:
            self.publisher.stop()

        logger.info("Stopped")


if __name__ == "__main__":
    creds = PlainCredentials("guest", "guest")
    upstream_rmq = ConnectionParameters(
        host=UPSTREAM_HOST,
        virtual_host="/",
        credentials=creds,
    )
    # The heartbeat value is set to a purposefully low number to demonstrate
    # that heartbeats are correctly handled
    downstream_rmq = ConnectionParameters(
        host=DOWNSTREAM_HOST,
        virtual_host="/",
        credentials=creds,
        heartbeat=10,
    )

    consumer = Consumer(upstream_rmq, downstream_rmq)
    logger.info(f"Started Consumer")
    logger.info(
        "Go to http://localhost:7001/#/queues/%2F/upstream_queue and post a message into the 'upstream_queue'."
    )
    consumer.start()
