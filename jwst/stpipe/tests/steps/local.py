from __future__ import absolute_import, division, print_function

from ....step import Step
import logging

log = logging.getLogger("FOO")
log.setLevel(logging.DEBUG)

class DummyStep(Step):
    """
    This is a dummy step that does dumb things.
    """

    spec = """
    foo = string()
    """

    def process(self, *args):
        from ....datamodels import ImageModel

        log.info("Default logger")
        log.debug("Default logger")

        self.log.info("Foo: {0}".format(self.foo))

        self.log.debug("Debug!!!")

        return ImageModel(args[0])
